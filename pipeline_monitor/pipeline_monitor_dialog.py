# -*- coding: utf-8 -*-

import os
import traceback
import logging  # Import the logging module
import sys  # Import sys for robust logging
from qgis.PyQt import uic
from qgis.PyQt.QtWidgets import QDialog, QListWidgetItem, QLabel
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtCore import Qt, QMetaType
from PyQt5.QtCore import QVariant, QSettings  # 添加QSettings导入
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
    QgsSingleSymbolRenderer,  # 用于行政规划图样式设置
)

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
        self.first_open = True  # 标记是否是首次打开插件
        self.data_loaded = False  # 标记数据是否已加载

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
            self.loadDataButton.clicked.connect(lambda: self.load_and_display_data())

        if hasattr(self, "identifyButton"):
            self.identifyButton.setCheckable(True)
            self.identifyButton.clicked.connect(self.activate_point_tool)

        # 连接底图下拉框的信号
        if hasattr(self, "baseMapComboBox"):
            self.baseMapComboBox.currentIndexChanged.connect(self.switch_base_map)

        # 初始化底图相关变量 (放在最后初始化，避免在对象创建过程中被访问导致问题)
        self.base_map_layers = {}
        self.current_base_map = None

    def set_iface(self, iface):
        self.iface = iface

        # 设置地图画布的坐标参考系统为Web Mercator (EPSG:3857)
        if self.iface and self.iface.mapCanvas():
            crs = QgsCoordinateReferenceSystem("EPSG:3857")
            self.iface.mapCanvas().setDestinationCrs(crs)
            self.logger.debug("已将地图画布坐标系设置为EPSG:3857 (Web Mercator)")

        # 设置界面接口后初始化底图
        self.initialize_base_maps()

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

    def initialize_base_maps(self):
        """初始化所有底图图层 - 一次性创建所有底图并添加到图层树，通过控制可见性来切换"""
        if not self.iface:
            self.logger.error("无法初始化底图：iface未设置")
            return

        self.logger.debug("开始初始化底图...")

        # 检查是否已经初始化过底图
        if self.base_map_layers and not self.first_open:
            self.logger.debug("底图已经初始化且非首次打开，保留当前视图")
            return

        # 清空现有底图字典
        self.base_map_layers = {}

        # 获取图层树根节点
        root = QgsProject.instance().layerTreeRoot()

        # 设置HTTP请求的用户代理
        settings = QSettings()
        settings.setValue(
            "qgis/networkAndProxy/userAgent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        )
        self.logger.debug("已设置HTTP请求的用户代理")

        try:
            # 1. 行政规划图（GeoJSON）
            existing_admin = self.find_existing_layer("行政规划图")
            if existing_admin:
                self.logger.debug("行政规划图已存在，使用现有图层")
                admin_layer = existing_admin
            else:
                admin_map_uri = (
                    "https://geo.datav.aliyun.com/areas_v3/bound/100000_full_city.json"
                )
                self.logger.debug(f"尝试加载行政规划图: {admin_map_uri}")
                admin_layer = QgsVectorLayer(admin_map_uri, "行政规划图", "ogr")

                if admin_layer.isValid():
                    # 应用样式
                    style_config = {
                        "fill_color": "#ffffff",  # 白色填充
                        "outline_color": "#000000",  # 黑色边框
                        "outline_style": "dash",  # 虚线边框
                        "outline_width": "0.1",  # 边框宽度
                    }

                    # 创建符号并设置样式
                    symbol = QgsSymbol.defaultSymbol(admin_layer.geometryType())
                    symbol.setColor(QColor(style_config["fill_color"]))

                    # 获取符号图层并设置边框属性
                    if hasattr(symbol, "symbolLayer") and symbol.symbolLayerCount() > 0:
                        symbol_layer = symbol.symbolLayer(0)
                        symbol_layer.setStrokeColor(
                            QColor(style_config["outline_color"])
                        )
                        symbol_layer.setStrokeWidth(
                            float(style_config["outline_width"])
                        )
                        symbol_layer.setStrokeStyle(Qt.DashLine)

                    # 应用符号到图层
                    renderer = QgsSingleSymbolRenderer(symbol)
                    admin_layer.setRenderer(renderer)

                    # 添加到项目但不在图层树显示
                    QgsProject.instance().addMapLayer(admin_layer, False)

                    # 添加到图层树的底部
                    child_count = len(root.children())
                    admin_node = QgsLayerTreeLayer(admin_layer)
                    root.insertChildNode(child_count, admin_node)
                    self.logger.debug("行政规划图已加载并添加到图层树底部")
                else:
                    self.logger.warning(f"行政规划图加载失败: {admin_map_uri}")
                    admin_layer = None

            if admin_layer:
                self.base_map_layers["admin"] = admin_layer
                # 默认隐藏图层
                root.findLayer(admin_layer.id()).setItemVisibilityChecked(False)

            # 2. 高德地形图（XYZ瓦片）
            existing_terrain = self.find_existing_layer("高德地形图")
            if existing_terrain:
                self.logger.debug("高德地形图已存在，使用现有图层")
                terrain_layer = existing_terrain
            else:
                # 使用更新后的URL格式
                terrain_url = "type=xyz&url=http-header:referer=&type=xyz&url=https://webst01.is.autonavi.com/appmaptile?style%3D6%26x%3D%7Bx%7D%26y%3D%7By%7D%26z%3D%7Bz%7D&zmax=19&zmin=0"
                self.logger.debug(f"尝试加载高德地形图: {terrain_url}")

                try:
                    terrain_layer = QgsRasterLayer(terrain_url, "高德地形图", "wms")
                    if terrain_layer.isValid():
                        self.logger.debug("高德地形图加载成功")
                        # 添加到项目但不在图层树显示
                        QgsProject.instance().addMapLayer(terrain_layer, False)

                        # 添加到图层树的底部
                        child_count = len(root.children())
                        terrain_node = QgsLayerTreeLayer(terrain_layer)
                        root.insertChildNode(child_count, terrain_node)
                        self.logger.debug("高德地形图已加载并添加到图层树底部")
                    else:
                        self.logger.warning("高德地形图加载失败")
                        terrain_layer = None
                except Exception as e:
                    self.logger.error(f"加载高德地形图时出错: {str(e)}")
                    terrain_layer = None

            if terrain_layer:
                self.base_map_layers["terrain"] = terrain_layer
                # 默认隐藏图层
                root.findLayer(terrain_layer.id()).setItemVisibilityChecked(False)

            # 3. 高德矢量图（XYZ瓦片）
            existing_vector = self.find_existing_layer("高德矢量图")
            if existing_vector:
                self.logger.debug("高德矢量图已存在，使用现有图层")
                vector_layer = existing_vector
            else:
                # 使用更新后的URL格式
                vector_url = "type=xyz&url=http-header:referer=&type=xyz&url=https://webrd02.is.autonavi.com/appmaptile?lang%3Dzh_cn%26size%3D1%26scale%3D1%26style%3D8%26x%3D%7Bx%7D%26y%3D%7By%7D%26z%3D%7Bz%7D&zmax=18&zmin=0"
                self.logger.debug(f"尝试加载高德矢量图: {vector_url}")

                try:
                    vector_layer = QgsRasterLayer(vector_url, "高德矢量图", "wms")
                    if vector_layer.isValid():
                        self.logger.debug("高德矢量图加载成功")
                        # 添加到项目但不在图层树显示
                        QgsProject.instance().addMapLayer(vector_layer, False)

                        # 添加到图层树的底部
                        child_count = len(root.children())
                        vector_node = QgsLayerTreeLayer(vector_layer)
                        root.insertChildNode(child_count, vector_node)
                        self.logger.debug("高德矢量图已加载并添加到图层树底部")
                    else:
                        self.logger.warning("高德矢量图加载失败")
                        vector_layer = None
                except Exception as e:
                    self.logger.error(f"加载高德矢量图时出错: {str(e)}")
                    vector_layer = None

            if vector_layer:
                self.base_map_layers["vector"] = vector_layer
                # 默认隐藏图层
                root.findLayer(vector_layer.id()).setItemVisibilityChecked(False)

            # 记录已加载的底图数量
            loaded_maps = sum(
                1
                for layer in self.base_map_layers.values()
                if layer and layer.isValid()
            )
            self.logger.info(
                f"底图初始化完成，成功加载 {loaded_maps}/{len(self.base_map_layers)} 个底图"
            )

            # 如果是首次打开，尝试读取上次使用的底图索引
            if self.first_open and hasattr(self, "baseMapComboBox"):
                settings = QSettings()
                last_index = settings.value(
                    "PipelineMonitor/lastBaseMapIndex", 0, type=int
                )

                # 确保索引在有效范围内
                if last_index >= 0 and last_index < self.baseMapComboBox.count():
                    self.baseMapComboBox.setCurrentIndex(last_index)
                    self.switch_base_map(last_index)
                else:
                    # 如果索引无效，使用默认值0
                    self.baseMapComboBox.setCurrentIndex(0)
                    self.switch_base_map(0)

                self.first_open = False  # 标记为非首次打开

        except Exception as e:
            self.logger.error(f"初始化底图时发生错误: {str(e)}")
            self.logger.error(traceback.format_exc())

    def switch_base_map(self, index):
        """根据下拉框选择切换底图 - 通过控制可见性"""
        if not self.iface:
            return

        self.logger.debug(f"切换底图，选择索引: {index}")

        # 检查底图是否已经初始化
        if not self.base_map_layers:
            self.logger.warning("底图尚未初始化，先执行initialize_base_maps")
            self.initialize_base_maps()
            return  # 初始化后会自动调用switch_base_map，这里直接返回

        # 获取图层树根节点
        root = QgsProject.instance().layerTreeRoot()

        # 先隐藏所有底图
        for layer in self.base_map_layers.values():
            if layer and layer.isValid():
                layer_node = root.findLayer(layer.id())
                if layer_node:
                    layer_node.setItemVisibilityChecked(False)

        # 根据索引获取对应底图类型
        map_type = None
        if index == 0:
            map_type = "admin"
        elif index == 1:
            map_type = "terrain"
        elif index == 2:
            map_type = "vector"
        else:
            self.logger.warning(f"未知底图索引: {index}")
            return

        # 显示选中的底图
        selected_layer = self.base_map_layers.get(map_type)
        if selected_layer and selected_layer.isValid():
            try:
                # 更新当前底图引用
                self.current_base_map = selected_layer

                # 设置为可见
                layer_node = root.findLayer(selected_layer.id())
                if layer_node:
                    layer_node.setItemVisibilityChecked(True)
                    self.logger.debug(f"显示底图: {selected_layer.name()}")
                    self.update_status_label(f"已切换底图: {selected_layer.name()}")

                    # 保存当前底图索引到QSettings，以便下次打开时恢复
                    settings = QSettings()
                    settings.setValue("PipelineMonitor/lastBaseMapIndex", index)
                    self.logger.debug(f"已保存当前底图索引: {index}")
                else:
                    self.logger.warning(f"找不到底图节点: {selected_layer.name()}")
            except Exception as e:
                self.logger.error(f"显示底图时出错: {str(e)}")
                self.update_status_label("切换底图时出现错误")
        else:
            self.logger.warning(f"选择的底图不存在或无效")
            self.update_status_label(f"底图未找到，请检查网络连接")

        # 确保数据图层在顶部
        self.ensure_data_layers_on_top()

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
            self.logger.debug(
                f"转换点击坐标从 {canvas_crs.authid()} 到 {layer_crs.authid()}"
            )
        else:
            self.logger.debug(f"画布和图层使用相同的坐标系: {canvas_crs.authid()}")

        # Web Mercator (EPSG:3857)坐标系下的搜索容差需要较大
        # 因为EPSG:3857的单位是米，而不是度
        tolerance = 50  # 50米的搜索半径

        # 创建一个搜索矩形
        search_rect = QgsRectangle(
            click_point_in_layer_crs.x() - tolerance,
            click_point_in_layer_crs.y() - tolerance,
            click_point_in_layer_crs.x() + tolerance,
            click_point_in_layer_crs.y() + tolerance,
        )

        # 创建一个要素请求
        request = QgsFeatureRequest().setFilterRect(search_rect)
        found_features = list(self.piles_layer.getFeatures(request))

        if found_features:
            self.logger.debug(f"Found {len(found_features)} feature(s) nearby.")

            # 找到最接近点击位置的要素
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

    def load_and_display_data(self, auto_zoom=False):
        """
        加载并显示数据

        参数:
            auto_zoom (bool): 是否自动缩放到图层范围，默认为False
        """
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

            # 检查图层是否已经存在
            existing_piles_layer = self.find_existing_layer("测试桩图层 (带风险状态)")
            existing_pipeline_layer = self.find_existing_layer("管线图层")

            # 记录是否需要创建新图层
            new_layers_created = False

            # 如果不存在测试桩图层或其引用已失效，则创建新图层
            if not existing_piles_layer:
                self.logger.debug("测试桩图层不存在或已失效，创建新图层")
                self.create_or_update_piles_layer(all_piles, latest_voltages)
                new_layers_created = True
            else:
                self.logger.debug("测试桩图层已存在，使用现有图层")
                self.piles_layer = existing_piles_layer

            # 如果不存在管线图层或其引用已失效，则创建新图层
            if not existing_pipeline_layer:
                self.logger.debug("管线图层不存在或已失效，创建新图层")
                self.create_or_update_pipeline_layer(all_piles)
                new_layers_created = True
            else:
                self.logger.debug("管线图层已存在，使用现有图层")
                self.pipeline_layer = existing_pipeline_layer

            # 更新测试桩列表
            self.populate_pile_list(all_piles)

            # 确保数据图层在顶部
            self.ensure_data_layers_on_top()

            # 只有在需要时才缩放到图层
            if auto_zoom:
                self.logger.debug("自动缩放到图层范围")
                self.zoom_to_layers()
            else:
                self.logger.debug("跳过自动缩放，保留当前视图")

            # 标记数据已加载
            self.data_loaded = True

            # 更新状态
            self.update_status_label(f"加载成功！共加载 {len(all_piles)} 个测试桩。")
        except Exception as e:
            self.update_status_label(
                f"发生错误: {type(e).__name__}。详情见Python控制台。"
            )
            self.logger.exception("插件执行时发生严重错误！")
        finally:
            if conn and conn.is_connected():
                conn.close()

    def create_or_update_piles_layer(self, piles_data, latest_voltages):
        """根据最新电压数据创建或更新测试桩的点图层，并应用分类渲染"""
        layer_name = "测试桩图层 (带风险状态)"
        self.remove_layer_by_name(layer_name)

        try:
            # 创建新图层，使用Web Mercator (EPSG:3857)投影
            vl = QgsVectorLayer("Point?crs=EPSG:3857", layer_name, "memory")
            pr = vl.dataProvider()

            # 检查图层和数据提供者是否有效
            if not vl.isValid() or not pr.isValid():
                self.logger.error(
                    f"创建测试桩图层失败: 图层有效性={vl.isValid()}, 提供者有效性={pr.isValid()}"
                )
                self.update_status_label("创建测试桩图层时出错")
                return

            # Connect destroyed signal to clean up reference
            try:
                vl.destroyed.connect(self._on_layer_destroyed)
                self.logger.debug("成功连接测试桩图层销毁信号")
            except Exception as e:
                self.logger.warning(f"连接测试桩图层销毁信号失败: {str(e)}")

            self.logger.debug(f"Piles layer initial validity: {vl.isValid()}")
            self.logger.debug(f"Data provider initial validity: {pr.isValid()}")
            self.logger.debug(f"Data provider capabilities: {pr.capabilities()}")

            # 添加字段
            try:
                # 使用推荐的QgsField构造方式
                pr.addAttributes(
                    [
                        QgsField("id", QMetaType.Int, "Integer"),
                        QgsField("name", QMetaType.QString, "String", 50),
                        QgsField("voltage", QMetaType.Double, "Double", 10, 3),
                        QgsField("risk_level", QMetaType.QString, "String", 20),
                    ]
                )
                vl.updateFields()
            except Exception as e:
                self.logger.error(f"添加测试桩图层字段时出错: {str(e)}")

            # 创建坐标转换器，从WGS84转换到Web Mercator
            source_crs = QgsCoordinateReferenceSystem("EPSG:4326")  # WGS84
            dest_crs = QgsCoordinateReferenceSystem("EPSG:3857")  # Web Mercator
            transform = QgsCoordinateTransform(
                source_crs, dest_crs, QgsProject.instance()
            )

            # Create features
            features_to_add = []
            for pile in piles_data:
                try:
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
                    # 将WGS84坐标转换为Web Mercator
                    point = QgsPointXY(pile["longitude"], pile["latitude"])
                    transformed_point = transform.transform(point)
                    feat.setGeometry(QgsGeometry.fromPointXY(transformed_point))
                    feat.setAttributes(
                        [pile["id"], pile["name"], current_voltage, risk_level]
                    )
                    features_to_add.append(feat)
                except Exception as e:
                    self.logger.error(
                        f"处理测试桩 ID {pile.get('id', 'unknown')} 时出错: {str(e)}"
                    )

            self.logger.debug(f"Number of features to add: {len(features_to_add)}")

            if features_to_add:
                try:
                    # Add features and commit changes
                    success = pr.addFeatures(features_to_add)
                    self.logger.debug(f"Features added successfully: {success}")

                    # Force update of layer
                    vl.updateExtents()
                    self.logger.debug(
                        f"Layer feature count after update: {vl.featureCount()}"
                    )
                except Exception as e:
                    self.logger.error(f"添加测试桩要素时出错: {str(e)}")

            # 应用分类渲染器
            try:
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
                    category = QgsRendererCategory(
                        rule["category"], symbol, rule["label"]
                    )
                    categories.append(category)

                renderer = QgsCategorizedSymbolRenderer(target_field, categories)
                vl.setRenderer(renderer)
                vl.triggerRepaint()
            except Exception as e:
                self.logger.error(f"设置测试桩渲染器时出错: {str(e)}")

            # 添加图层到项目
            try:
                QgsProject.instance().addMapLayer(vl)
                self.piles_layer = vl
                self.logger.debug(f"测试桩图层已添加到项目, ID: {vl.id()}")
            except Exception as e:
                self.logger.error(f"将测试桩图层添加到项目时出错: {str(e)}")
                return
        except Exception as e:
            self.logger.error(f"创建测试桩图层时发生未知错误: {str(e)}")
            self.logger.error(traceback.format_exc())
            self.update_status_label("创建测试桩图层时出错")

    def create_or_update_pipeline_layer(self, piles_data):
        """创建或更新管线的线图层"""
        layer_name = "管线图层"
        self.remove_layer_by_name(layer_name)

        try:
            # 创建新图层，使用Web Mercator (EPSG:3857)投影
            vl = QgsVectorLayer("LineString?crs=EPSG:3857", layer_name, "memory")
            pr = vl.dataProvider()

            # 检查图层和数据提供者是否有效
            if not vl.isValid() or not pr.isValid():
                self.logger.error(
                    f"创建管线图层失败: 图层有效性={vl.isValid()}, 提供者有效性={pr.isValid()}"
                )
                self.update_status_label("创建管线图层时出错")
                return

            # Connect destroyed signal to clean up reference
            try:
                vl.destroyed.connect(self._on_layer_destroyed)
                self.logger.debug("成功连接管线图层销毁信号")
            except Exception as e:
                self.logger.warning(f"连接管线图层销毁信号失败: {str(e)}")

            # 创建坐标转换器，从WGS84转换到Web Mercator
            source_crs = QgsCoordinateReferenceSystem("EPSG:4326")  # WGS84
            dest_crs = QgsCoordinateReferenceSystem("EPSG:3857")  # Web Mercator
            transform = QgsCoordinateTransform(
                source_crs, dest_crs, QgsProject.instance()
            )

            # 添加管线数据
            points = []
            for p in piles_data:
                # 转换每个点的坐标
                point = QgsPointXY(p["longitude"], p["latitude"])
                transformed_point = transform.transform(point)
                points.append(transformed_point)

            if len(points) > 1:
                try:
                    feat = QgsFeature()
                    line = QgsLineString(points)
                    feat.setGeometry(QgsGeometry.fromWkt(line.asWkt()))
                    pr.addFeature(feat)
                    self.logger.debug("成功添加管线要素")
                except Exception as e:
                    self.logger.error(f"添加管线要素时出错: {str(e)}")

            # Add layer to project
            try:
                QgsProject.instance().addMapLayer(vl)
                self.pipeline_layer = vl
                self.logger.debug(f"管线图层已添加到项目, ID: {vl.id()}")
            except Exception as e:
                self.logger.error(f"将管线图层添加到项目时出错: {str(e)}")
                return
        except Exception as e:
            self.logger.error(f"创建管线图层时发生未知错误: {str(e)}")
            self.logger.error(traceback.format_exc())
            self.update_status_label("创建管线图层时出错")

    def remove_layer_by_name(self, layer_name):
        # Get all layers currently in project as a list for safe iteration
        layers_in_project = list(QgsProject.instance().mapLayers().values())

        # Proceed with explicit removal by name from the project
        layers_to_remove = QgsProject.instance().mapLayersByName(layer_name)
        if layers_to_remove:
            self.logger.debug(f"Removing layer by name: {layer_name}")
            removed_layer = layers_to_remove[0]
            layer_id = removed_layer.id()  # 在移除前保存ID

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

            if self.current_base_map is removed_layer:
                self.current_base_map = None
                self.logger.debug(
                    f"Cleared self.current_base_map reference for {layer_name}."
                )

            # 移除图层
            QgsProject.instance().removeMapLayer(layer_id)
            self.logger.debug(f"Successfully removed layer with ID: {layer_id}")
        else:
            self.logger.debug(f"Layer '{layer_name}' not found for explicit removal.")

    def ensure_data_layers_on_top(self):
        """确保数据图层（测试桩和管线）显示在最上层，底图保持在底层"""
        if not self.iface:
            return

        # 获取图层树根节点
        root = QgsProject.instance().layerTreeRoot()

        # 查找测试桩和管线图层
        data_layers = []
        for layer_name in ["测试桩图层 (带风险状态)", "管线图层"]:
            layers = QgsProject.instance().mapLayersByName(layer_name)
            if layers:
                data_layers.append(layers[0])

                # 更新内部引用
                if layer_name == "测试桩图层 (带风险状态)" and (
                    self.piles_layer is None or not self.piles_layer.isValid()
                ):
                    self.piles_layer = layers[0]
                elif layer_name == "管线图层" and (
                    self.pipeline_layer is None or not self.pipeline_layer.isValid()
                ):
                    self.pipeline_layer = layers[0]

        if not data_layers:
            return

        # 按顺序处理数据图层，确保它们按期望的顺序放在顶部
        # 先处理管线图层，后处理测试桩图层，这样测试桩会在最顶部
        for layer_name in ["管线图层", "测试桩图层 (带风险状态)"]:
            for layer in data_layers:
                if layer.name() == layer_name:
                    try:
                        # 获取图层节点
                        node_layer = root.findLayer(layer.id())
                        if node_layer:
                            # 获取父节点
                            parent = node_layer.parent()
                            if parent:
                                # 克隆节点
                                cloned_node = node_layer.clone()
                                # 在根节点索引0处插入克隆的节点(索引0是顶部)
                                root.insertChildNode(0, cloned_node)
                                # 删除原始节点
                                parent.removeChildNode(node_layer)
                    except Exception as e:
                        self.logger.error(
                            f"移动图层 {layer.name()} 到顶部时出错: {str(e)}"
                        )

        # 刷新画布以应用更改
        try:
            self.iface.mapCanvas().refresh()
        except Exception as e:
            self.logger.error(f"刷新画布时出错: {str(e)}")

    def _on_layer_destroyed(self, destroyed_qobject):
        """处理图层被销毁的情况，清理内部引用"""
        # 清理内部引用
        if self.piles_layer is destroyed_qobject:
            self.piles_layer = None
            self.logger.debug("测试桩图层引用已清理")

        if self.pipeline_layer is destroyed_qobject:
            self.pipeline_layer = None
            self.logger.debug("管线图层引用已清理")

        if self.current_base_map is destroyed_qobject:
            self.current_base_map = None
            self.logger.debug("当前底图引用已清理")

    def populate_pile_list(self, piles_data):
        if hasattr(self, "pileListWidget"):
            self.pileListWidget.clear()
            for pile in piles_data:
                item_text = f"ID: {pile['id']} - {pile['name']}"
                self.pileListWidget.addItem(QListWidgetItem(item_text))

    def update_status_label(self, message):
        if hasattr(self, "statusLabel"):
            self.statusLabel.setText(message)

    def zoom_to_layers(self, preserve_scale=False):
        """
        缩放到测试桩和管线图层的范围

        参数:
            preserve_scale (bool): 如果为True，则保持当前比例尺不变，只移动到图层中心
        """
        if not self.iface:
            return

        # 查找有效的测试桩图层
        valid_piles_layer = (
            self.piles_layer
            if self.piles_layer and self.piles_layer.isValid()
            else None
        )
        valid_pipeline_layer = (
            self.pipeline_layer
            if self.pipeline_layer and self.pipeline_layer.isValid()
            else None
        )

        # 如果现有对象无效，尝试通过名称查找
        if not valid_piles_layer:
            layers = QgsProject.instance().mapLayersByName("测试桩图层 (带风险状态)")
            if layers:
                valid_piles_layer = layers[0]
                self.piles_layer = layers[0]  # 更新引用

        if not valid_pipeline_layer:
            layers = QgsProject.instance().mapLayersByName("管线图层")
            if layers:
                valid_pipeline_layer = layers[0]
                self.pipeline_layer = layers[0]  # 更新引用

        # 如果有有效的测试桩图层，则缩放到范围
        if valid_piles_layer:
            canvas = self.iface.mapCanvas()
            full_extent = valid_piles_layer.extent()

            # 如果有管线图层，则合并范围
            if valid_pipeline_layer:
                full_extent.combineExtentWith(valid_pipeline_layer.extent())

            # 扩大范围，留出边距
            full_extent.grow(0.1)

            if preserve_scale:
                # 只移动到图层中心，保持当前比例尺
                center = full_extent.center()
                current_extent = canvas.extent()
                width = current_extent.width()
                height = current_extent.height()
                new_extent = QgsRectangle(
                    center.x() - width / 2,
                    center.y() - height / 2,
                    center.x() + width / 2,
                    center.y() + height / 2,
                )
                canvas.setExtent(new_extent)
                self.logger.debug("移动到图层中心，保持当前比例尺")
            else:
                # 设置画布范围并刷新
                canvas.setExtent(full_extent)
                self.logger.debug("缩放到图层完整范围")

            canvas.refresh()
            self.logger.debug("成功更新视图")

    def find_existing_layer(self, layer_name):
        """
        查找项目中指定名称的图层，如找到则返回图层对象，否则返回None
        """
        layers = QgsProject.instance().mapLayersByName(layer_name)
        if layers and len(layers) > 0:
            layer = layers[0]
            if layer.isValid():
                return layer
        return None

    def showEvent(self, event):
        """当对话框显示时调用，确保不会重置视图"""
        super(PipelineMonitorDialog, self).showEvent(event)

        # 如果不是首次打开，则不进行任何视图重置操作
        if not self.first_open:
            self.logger.debug("对话框再次打开，保留当前视图")
        else:
            # 首次打开时初始化底图
            if self.iface:
                self.logger.debug("对话框首次打开，初始化底图")
                self.initialize_base_maps()

                # 如果数据尚未加载，则加载数据
                if not self.data_loaded:
                    self.logger.debug("首次打开，加载数据并保持当前比例尺")
                    self.load_and_display_data(auto_zoom=False)
                    # 移动到图层中心但保持当前比例尺
                    self.zoom_to_layers(preserve_scale=True)
                    self.data_loaded = True
