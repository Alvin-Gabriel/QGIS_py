# -*- coding: utf-8 -*-

import os
import traceback
import logging  # Import the logging module
import sys  # Import sys for robust logging
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
    QgsRasterLayer,
    QgsLayerTree,
    QgsLayerTreeLayer,
    QgsMessageLog,
    Qgis,  # Import Qgis for message levels
)
from qgis.PyQt.QtCore import QVariant

from .map_tool import PointTool
from .pile_details_dialog import PileDetailsDialog
from . import db_operations


# Custom logging handler for QGIS Message Log
class QgsMessageLogHandler(logging.Handler):
    def emit(self, record):
        msg = self.format(record)
        # Map logging levels to QgsMessageLog message types using Qgis enum
        if record.levelno >= logging.CRITICAL:
            QgsMessageLog.logMessage(
                msg, "PipelineMonitor", Qgis.Critical
            )  # Use Qgis.Critical
        elif record.levelno >= logging.ERROR:
            QgsMessageLog.logMessage(
                msg, "PipelineMonitor", Qgis.Critical
            )  # Map ERROR to Critical for visibility
        elif record.levelno >= logging.WARNING:
            QgsMessageLog.logMessage(
                msg, "PipelineMonitor", Qgis.Warning
            )  # Use Qgis.Warning
        elif record.levelno >= logging.INFO:
            QgsMessageLog.logMessage(msg, "PipelineMonitor", Qgis.Info)  # Use Qgis.Info
        else:  # DEBUG and NOTSET
            QgsMessageLog.logMessage(
                msg, "PipelineMonitor", Qgis.Info
            )  # Map DEBUG to Info


FORM_CLASS, _ = uic.loadUiType(
    os.path.join(os.path.dirname(__file__), "pipeline_monitor_dialog_base.ui")
)


class PipelineMonitorDialog(QDialog, FORM_CLASS):
    def __init__(self, parent=None):
        super(PipelineMonitorDialog, self).__init__(parent)
        self.setupUi(self)
        self.piles_layer = None
        self.pipeline_layer = None
        self.iface = None
        self.point_tool = None

        # Initialize logger
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.propagate = False

        # Remove all existing StreamHandlers
        for handler in list(self.logger.handlers):
            if isinstance(handler, logging.StreamHandler):
                self.logger.removeHandler(handler)
                handler.close()
        root_logger = logging.getLogger()
        for handler in list(root_logger.handlers):
            if isinstance(handler, logging.StreamHandler):
                root_logger.removeHandler(handler)
                handler.close()

        # Add QgsMessageLogHandler
        if not any(isinstance(h, QgsMessageLogHandler) for h in self.logger.handlers):
            qgis_handler = QgsMessageLogHandler()
            formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
            qgis_handler.setFormatter(formatter)
            self.logger.addHandler(qgis_handler)

        self.logger.setLevel(logging.DEBUG)
        self.logger.debug(
            "PipelineMonitorDialog logger initialized with QgsMessageLog."
        )

        if hasattr(self, "loadDataButton"):
            self.loadDataButton.clicked.connect(self.load_and_display_data)

        if hasattr(self, "identifyButton"):
            self.identifyButton.setCheckable(True)
            self.identifyButton.clicked.connect(self.activate_point_tool)

    def set_iface(self, iface):
        self.iface = iface

    def calculate_risk_level(self, voltage):
        """根据电压值计算风险等级字符串"""
        if voltage is None or voltage == 9999.0:
            return "未知"
        elif voltage < -1.2:
            return "过保护"
        elif -1.2 <= voltage <= -0.85:
            return "正常"
        else:
            return "欠保护"

    def activate_point_tool(self):
        try:
            if not (self.piles_layer and self.piles_layer.isValid()):
                self.update_status_label("请先加载测试桩图层！")
                self.identifyButton.setChecked(False)
                return
        except RuntimeError as e:
            self.logger.warning(
                f"RuntimeError in activate_point_tool accessing piles_layer: {e}. Clearing reference and returning."
            )
            self.update_status_label("测试桩图层已失效，请重新加载数据。")
            self.piles_layer = None  # Clear the invalid reference
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
        self.logger.debug("-" * 50)
        self.logger.debug(
            f"Map clicked at (Canvas CRS): {point.x():.6f}, {point.y():.6f}"
        )

        if not self.piles_layer:
            self.logger.debug("DEBUG: Error, self.piles_layer is None!")
            return

        self.logger.debug(
            f"Searching in layer '{self.piles_layer.name()}' which has {self.piles_layer.featureCount()} features."
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
            self.logger.debug(f"DEBUG: CRS match (both are: {canvas_crs.authid()}).")

        # Define a search tolerance in map units (e.g., 5 map units)
        tolerance = 5  # Adjust this value as needed

        # Create a search rectangle around the clicked point
        search_rect = QgsRectangle(
            click_point_in_layer_crs.x() - tolerance,
            click_point_in_layer_crs.y() - tolerance,
            click_point_in_layer_crs.x() + tolerance,
            click_point_in_layer_crs.y() + tolerance,
        )

        # Create a feature request with the search rectangle
        request = QgsFeatureRequest().setFilterRect(search_rect)
        found_features = list(self.piles_layer.getFeatures(request))

        if found_features:
            self.logger.debug(f"Found {len(found_features)} feature(s) nearby.")

            # Find the closest feature to the click point
            closest_feature = None
            min_distance = float("inf")

            for feature in found_features:
                if feature.geometry() and not feature.geometry().isEmpty():
                    distance = (
                        feature.geometry()
                        .centroid()
                        .asPoint()
                        .distance(click_point_in_layer_crs)
                    )
                    if distance < min_distance:
                        min_distance = distance
                        closest_feature = feature

            if closest_feature:
                self.show_details_dialog(closest_feature)
                self.logger.debug(
                    f"Selected feature for dialog - ID: {closest_feature.attribute('id')}, Name: {closest_feature.attribute('name')}, Voltage: {closest_feature.attribute('voltage')}, Risk: {closest_feature.attribute('risk_level')}"
                )
            else:
                self.logger.debug("No valid feature found among nearby features.")
                self.iface.messageBar().pushMessage(
                    "提示", "未找到附近的测试桩。", level=Qgis.Info
                )
        else:
            self.logger.debug("No feature found nearby.")
            self.iface.messageBar().pushMessage(
                "提示", "未找到附近的测试桩。", level=Qgis.Info
            )
        self.logger.debug("-" * 50)

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
            self.logger.debug("成功连接到数据库，正在获取测试桩和电压数据...")
            all_piles = db_operations.get_all_test_piles(conn)
            latest_voltages = db_operations.get_latest_voltages(conn)

            if not all_piles:
                self.update_status_label("警告: 数据库中没有测试桩数据。")
                if conn.is_connected():
                    conn.close()
                return
            self.create_or_update_piles_layer(all_piles, latest_voltages)
            self.create_or_update_pipeline_layer(all_piles)
            self.populate_pile_list(all_piles)
            self.zoom_to_layers()
            self.update_status_label(f"加载成功！共加载 {len(all_piles)} 个测试桩。")
        except Exception as e:
            error_details = traceback.format_exc()
            self.update_status_label(
                f"发生错误: {type(e).__name__}。详情见Python控制台。"
            )
            self.logger.exception("插件执行时发生严重错误！")
            print(error_details)
        finally:
            if conn and conn.is_connected():
                conn.close()
        self._log_layer_tree_structure(
            "After initial data load and layer creation"
        )  # Log after initial load

        # Debugging: Check layer validity before calling ensure_main_layers_on_top
        piles_valid = self.piles_layer and self.piles_layer.isValid()
        pipeline_valid = self.pipeline_layer and self.pipeline_layer.isValid()
        self.logger.debug(
            f"Before ensure_main_layers_on_top: piles_layer valid = {piles_valid}, pipeline_layer valid = {pipeline_valid}"
        )

        self.ensure_main_layers_on_top()

    def create_or_update_piles_layer(self, piles_data, latest_voltages):
        """根据最新电压数据创建或更新测试桩的点图层，并应用分类渲染"""
        layer_name = "测试桩图层 (带风险状态)"
        self.remove_layer_by_name(layer_name)

        vl = QgsVectorLayer("Point?crs=EPSG:4326", layer_name, "memory")
        pr = vl.dataProvider()

        # Connect destroyed signal to clean up reference
        vl.destroyed.connect(self._on_layer_destroyed)

        self.logger.debug(f"Piles layer initial validity: {vl.isValid()}")
        self.logger.debug(f"Data provider initial validity: {pr.isValid()}")
        self.logger.debug(f"Data provider capabilities: {pr.capabilities()}")

        # Add fields
        pr.addAttributes(
            [
                QgsField("id", QVariant.Int),
                QgsField("name", QVariant.String),
                QgsField("voltage", QVariant.Double),
                QgsField("risk_level", QVariant.String),
            ]
        )
        vl.updateFields()

        # Create features
        features_to_add = []
        for pile in piles_data:
            pile_id = pile["id"]
            current_voltage_info = latest_voltages.get(pile_id)
            self.logger.debug(
                f"DEBUG: pile_id: {pile_id}, current_voltage_info type: {type(current_voltage_info)}, value: {current_voltage_info}"
            )

            current_voltage = (
                float(current_voltage_info["voltage"])
                if current_voltage_info
                and current_voltage_info.get("voltage") is not None
                else 9999.0  # Use 9999.0 for unknown/missing latest voltage
            )
            self.logger.debug(
                f"DEBUG: current_voltage type: {type(current_voltage)}, value: {current_voltage}"
            )
            risk_level = self.calculate_risk_level(current_voltage)

            feat = QgsFeature()
            point = QgsPointXY(pile["longitude"], pile["latitude"])
            feat.setGeometry(QgsGeometry.fromPointXY(point))
            feat.setAttributes([pile["id"], pile["name"], current_voltage, risk_level])
            features_to_add.append(feat)

        self.logger.debug(f"Number of features to add: {len(features_to_add)}")

        if features_to_add:
            # Add features and commit changes
            success = pr.addFeatures(features_to_add)
            self.logger.debug(f"Features added successfully: {success}")

            # Force update of layer
            vl.updateExtents()
            self.logger.debug(f"Layer feature count after update: {vl.featureCount()}")

        # Apply categorized renderer
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

        # Add layer to project
        QgsProject.instance().addMapLayer(vl)
        self.piles_layer = vl

        # Ensure layer is on top
        self.ensure_main_layers_on_top()

    def create_or_update_pipeline_layer(self, piles_data):
        """创建或更新管线的线图层"""
        layer_name = "管线图层"
        self.remove_layer_by_name(layer_name)

        vl = QgsVectorLayer("LineString?crs=EPSG:4326", layer_name, "memory")
        pr = vl.dataProvider()

        # Connect destroyed signal to clean up reference
        vl.destroyed.connect(self._on_layer_destroyed)

        points = [QgsPointXY(p["longitude"], p["latitude"]) for p in piles_data]
        if len(points) > 1:
            feat = QgsFeature()
            line = QgsLineString(points)
            feat.setGeometry(QgsGeometry.fromWkt(line.asWkt()))
            pr.addFeature(feat)

        # Add layer to project
        QgsProject.instance().addMapLayer(vl)
        self.pipeline_layer = vl

        # Ensure layer is on top
        self.ensure_main_layers_on_top()

    def remove_layer_by_name(self, layer_name):
        # Get all layers currently in project as a list for safe iteration
        layers_in_project = list(QgsProject.instance().mapLayers().values())

        # Proceed with explicit removal by name from the project
        layers_to_remove = QgsProject.instance().mapLayersByName(layer_name)
        if layers_to_remove:
            self.logger.debug(f"Removing layer by name: {layer_name}")
            removed_layer = layers_to_remove[0]

            # Disconnect the destroyed signal for this specific layer
            # This prevents _on_layer_destroyed from being called for this layer's removal
            try:
                removed_layer.destroyed.disconnect(self._on_layer_destroyed)
            except (
                TypeError
            ):  # Signal might already be disconnected or not connected for this instance
                pass

            # Clear our internal references *before* removing from QgsProject
            if self.piles_layer is removed_layer:
                self.piles_layer = None
                self.logger.debug(
                    f"Cleared self.piles_layer reference for {layer_name}."
                )

            if self.pipeline_layer is removed_layer:
                self.pipeline_layer = None
                self.logger.debug(
                    f"Cleared self.pipeline_layer reference for {layer_name}."
                )

            QgsProject.instance().removeMapLayer(removed_layer.id())
            self.logger.debug(
                f"Successfully removed layer with ID: {removed_layer.id()}"
            )
        else:
            self.logger.debug(f"Layer '{layer_name}' not found for explicit removal.")

    def ensure_main_layers_on_top(self):
        """确保主图层（测试桩和管线）显示在最上层"""
        if not self.iface:
            return

        root = QgsProject.instance().layerTreeRoot()

        # Get the layers we want to move to top
        layers_to_move = []
        if self.piles_layer and self.piles_layer.isValid():
            layers_to_move.append(self.piles_layer)
        if self.pipeline_layer and self.pipeline_layer.isValid():
            layers_to_move.append(self.pipeline_layer)

        # Move layers to top
        for layer in layers_to_move:
            node = root.findLayer(layer.id())
            if node:
                clone = node.clone()
                parent = node.parent()
                parent.insertChildNode(-1, clone)
                parent.removeChildNode(node)

        self.iface.mapCanvas().refresh()

    def _log_layer_tree_structure(self, context="Current Layer Tree"):
        """Logs the current structure of the QGIS layer tree."""
        self.logger.debug(f"--- {context} --- ")
        root = QgsProject.instance().layerTreeRoot()
        self._log_node(root, 0)
        self.logger.debug(f"--- End of {context} ---")

    def _log_node(self, node, level):
        indent = "  " * level
        if node.nodeType() == QgsLayerTree.NodeLayer:
            layer = node.layer()
            if layer:
                self.logger.debug(f"{indent}Layer: {layer.name()} (ID: {layer.id()})")
            else:
                self.logger.debug(f"{indent}Layer (Invalid): {node.name()}")
        elif node.nodeType() == QgsLayerTree.NodeGroup:
            self.logger.debug(f"{indent}Group: {node.name()}")

        for child in node.children():
            self._log_node(child, level + 1)

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

    def _on_layer_destroyed(self, destroyed_qobject):
        self.logger.debug(
            f"A QObject was destroyed. Checking if it's a layer we manage. Destroyed QObject (type: {type(destroyed_qobject).__name__})."
        )

        # Clear our internal references if the destroyed object is one of our managed layers
        if self.piles_layer is destroyed_qobject:
            self.piles_layer = None
            self.logger.debug("Cleared self.piles_layer reference as it was destroyed.")

        if self.pipeline_layer is destroyed_qobject:
            self.pipeline_layer = None
            self.logger.debug(
                "Cleared self.pipeline_layer reference as it was destroyed."
            )
