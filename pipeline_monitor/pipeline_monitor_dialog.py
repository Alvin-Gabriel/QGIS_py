# -*- coding: utf-8 -*-

import os
import traceback
from qgis.PyQt import uic
from qgis.PyQt.QtWidgets import QDialog, QListWidgetItem, QLabel
from qgis.PyQt.QtGui import QColor
from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsField,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsLineString,
    QgsWkbTypes,
    QgsCategorizedSymbolRenderer,
    QgsSymbol,
    QgsRendererCategory,
    QgsMarkerSymbol,
    QgsRectangle,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeatureRequest,
)
from qgis.PyQt.QtCore import QVariant

from .map_tool import PointTool
from .pile_details_dialog import PileDetailsDialog
from . import db_operations

FORM_CLASS, _ = uic.loadUiType(
    os.path.join(os.path.dirname(__file__), "pipeline_monitor_dialog_base.ui")
)


def calculate_risk_level(voltage):
    """根据电压值计算风险等级字符串"""
    if voltage is None:
        return "未知"
    # 请根据您的业务标准调整阈值
    if voltage < -1.15:
        return "过保护"
    if -1.15 <= voltage < -0.85:
        return "正常"
    return "欠保护"


class PipelineMonitorDialog(QDialog, FORM_CLASS):
    def __init__(self, parent=None):
        super(PipelineMonitorDialog, self).__init__(parent)
        self.setupUi(self)
        self.piles_layer = None
        self.pipeline_layer = None
        self.iface = None
        self.point_tool = None

        if hasattr(self, "loadDataButton"):
            self.loadDataButton.clicked.connect(self.load_and_display_data)

        if hasattr(self, "identifyButton"):
            self.identifyButton.setCheckable(True)
            self.identifyButton.clicked.connect(self.activate_point_tool)

    def set_iface(self, iface):
        self.iface = iface

    def activate_point_tool(self):
        if not self.piles_layer:
            self.update_status_label("请先加载测试桩图层！")
            self.identifyButton.setChecked(False)
            return

        if self.identifyButton.isChecked():
            self.update_status_label("识别工具已激活：请在地图上点击测试桩。")
            self.point_tool = PointTool(self.iface.mapCanvas())
            self.point_tool.canvasClicked.connect(self.handle_canvas_click)
            self.iface.mapCanvas().setMapTool(self.point_tool)
        else:
            self.update_status_label("识别工具已取消。")
            if self.point_tool:
                self.iface.mapCanvas().unsetMapTool(self.point_tool)

    def handle_canvas_click(self, point, button):
        print("-" * 50)
        print(f"DEBUG: Map clicked at (Canvas CRS): {point.x():.6f}, {point.y():.6f}")

        if not self.piles_layer:
            print("DEBUG: Error, self.piles_layer is None!")
            return

        print(
            f"DEBUG: Searching in layer '{self.piles_layer.name()}' which has {self.piles_layer.featureCount()} features."
        )

        canvas = self.iface.mapCanvas()
        layer_crs = self.piles_layer.crs()
        canvas_crs = canvas.mapSettings().destinationCrs()

        click_point_in_layer_crs = point
        if layer_crs.authid() != canvas_crs.authid():
            transform = QgsCoordinateTransform(
                canvas_crs, layer_crs, QgsProject.instance()
            )
            click_point_in_layer_crs = transform.transform(point)
        else:
            print(f"DEBUG: CRS match (both are: {canvas_crs.authid()}).")

        tolerance = 0.0005
        search_rect = QgsRectangle.fromCenterAndSize(
            click_point_in_layer_crs, tolerance, tolerance
        )

        request = QgsFeatureRequest().setFilterRect(search_rect)
        found_features = list(self.piles_layer.getFeatures(request))

        if found_features:
            print(f"DEBUG: Found {len(found_features)} feature(s) nearby.")
            self.show_details_dialog(found_features[0])
        else:
            print("DEBUG: No features found at the specified location.")
        print("-" * 50)

    def show_details_dialog(self, feature):
        dialog = PileDetailsDialog(feature, self)
        dialog.exec_()

    def closeEvent(self, event):
        if self.point_tool and self.iface:
            self.iface.mapCanvas().unsetMapTool(self.point_tool)
        super(PipelineMonitorDialog, self).closeEvent(event)

    def load_and_display_data(self):
        self.update_status_label("正在连接数据库并加载数据...")
        conn = db_operations.create_connection()
        if not (conn and conn.is_connected()):
            self.update_status_label("错误: 无法连接到数据库。")
            return
        try:
            print("成功连接到数据库，正在获取测试桩和电压数据...")
            all_piles = db_operations.get_all_test_piles(conn)
            latest_voltages = db_operations.get_latest_voltages(conn)
            if not all_piles:
                self.update_status_label("警告: 数据库中没有测试桩数据。")
                if conn.is_connected():
                    conn.close()
                return
            self.create_or_update_piles_layer(all_piles, latest_voltages)
            # *** 修正您指出的拼写错误 ***
            self.create_or_update_pipeline_layer(all_piles)
            self.populate_pile_list(all_piles)
            self.zoom_to_layers()
            self.update_status_label(f"加载成功！共加载 {len(all_piles)} 个测试桩。")
        except Exception as e:
            error_details = traceback.format_exc()
            self.update_status_label(
                f"发生错误: {type(e).__name__}。详情见Python控制台。"
            )
            print("插件执行时发生严重错误！")
            print(error_details)
        finally:
            if conn and conn.is_connected():
                conn.close()

    def create_or_update_piles_layer(self, piles_data, latest_voltages):
        """根据最新电压数据创建或更新测试桩的点图层，并应用分类渲染"""
        layer_name = "测试桩图层 (带风险状态)"
        self.remove_layer_by_name(layer_name)
        vl = QgsVectorLayer("Point?crs=EPSG:4326", layer_name, "memory")
        pr = vl.dataProvider()
        pr.addAttributes(
            [
                QgsField("id", QVariant.Int),
                QgsField("name", QVariant.String),
                QgsField("voltage", QVariant.Double),
                QgsField("risk_level", QVariant.String),
            ]
        )
        vl.updateFields()
        for pile in piles_data:
            pile_id = pile["id"]
            latest_voltage_info = latest_voltages.get(pile_id)
            current_voltage = (
                latest_voltage_info["voltage"] if latest_voltage_info else None
            )
            risk_level = calculate_risk_level(current_voltage)
            feat = QgsFeature()
            # 使用已确保为float的坐标
            point = QgsPointXY(pile["longitude"], pile["latitude"])
            feat.setGeometry(QgsGeometry.fromPointXY(point))
            feat.setAttributes([pile["id"], pile["name"], current_voltage, risk_level])
            pr.addFeature(feat)

        target_field = "risk_level"
        symbol_rules = [
            {"category": "过保护", "label": "过保护", "color": "#0000FF"},
            {"category": "正常", "label": "正常", "color": "#00FF00"},
            {"category": "欠保护", "label": "欠保护", "color": "#FF0000"},
            {"category": "未知", "label": "未知/无数据", "color": "#808080"},
        ]

        categories = []
        for rule in symbol_rules:
            symbol = QgsMarkerSymbol.createSimple(
                {"name": "circle", "size": "4", "color": rule["color"]}
            )
            category = QgsRendererCategory(rule["category"], symbol, rule["label"])
            categories.append(category)

        renderer = QgsCategorizedSymbolRenderer(target_field, categories)
        vl.setRenderer(renderer)
        vl.triggerRepaint()

        QgsProject.instance().addMapLayer(vl)
        self.piles_layer = vl

    def create_or_update_pipeline_layer(self, piles_data):
        """创建或更新管线的线图层"""
        layer_name = "管线图层"
        self.remove_layer_by_name(layer_name)
        vl = QgsVectorLayer("LineString?crs=EPSG:4326", layer_name, "memory")
        pr = vl.dataProvider()
        points = [QgsPointXY(p["longitude"], p["latitude"]) for p in piles_data]
        if len(points) > 1:
            feat = QgsFeature()
            line = QgsLineString(points)
            feat.setGeometry(QgsGeometry.fromWkt(line.asWkt()))
            pr.addFeature(feat)
        QgsProject.instance().addMapLayer(vl)
        self.pipeline_layer = vl

    def remove_layer_by_name(self, layer_name):
        layers = QgsProject.instance().mapLayersByName(layer_name)
        if layers:
            QgsProject.instance().removeMapLayer(layers[0].id())

    def populate_pile_list(self, piles_data):
        if hasattr(self, "pileListWidget"):
            self.pileListWidget.clear()
            for pile in piles_data:
                item_text = f"ID: {pile['id']} - {pile['name']}"
                self.pileListWidget.addItem(QListWidgetItem(item_text))

    def update_status_label(self, message):
        if hasattr(self, "statusLabel"):
            self.statusLabel.setText(message)

    def zoom_to_layers(self):
        if self.piles_layer and self.iface:
            canvas = self.iface.mapCanvas()
            full_extent = self.piles_layer.extent()
            if self.pipeline_layer:
                full_extent.combineExtentWith(self.pipeline_layer.extent())

            full_extent.grow(0.1)
            canvas.setExtent(full_extent)
            canvas.refresh()
