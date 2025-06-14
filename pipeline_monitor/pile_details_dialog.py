# -*- coding: utf-8 -*-

import os
from qgis.PyQt import uic
from qgis.PyQt.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QWidget
from PyQt5.QtCore import Qt
import logging  # Import logging

# Import Qgis for message levels
from qgis.core import QgsMessageLog, Qgis


# Custom logging handler for QGIS Message Log
class QgsMessageLogHandler(logging.Handler):
    def emit(self, record):
        msg = self.format(record)
        # Map logging levels to QgsMessageLog message types using Qgis enum
        if record.levelno >= logging.CRITICAL:
            QgsMessageLog.logMessage(msg, "PipelineMonitor", Qgis.Critical)
        elif record.levelno >= logging.ERROR:
            QgsMessageLog.logMessage(msg, "PipelineMonitor", Qgis.Critical)
        elif record.levelno >= logging.WARNING:
            QgsMessageLog.logMessage(msg, "PipelineMonitor", Qgis.Warning)
        elif record.levelno >= logging.INFO:
            QgsMessageLog.logMessage(msg, "PipelineMonitor", Qgis.Info)
        else:  # DEBUG and NOTSET
            QgsMessageLog.logMessage(msg, "PipelineMonitor", Qgis.Info)


# Initialize logger for this module
logger = logging.getLogger(__name__)
logger.propagate = (
    False  # Crucial: Prevent messages from being passed to the root logger
)

# Remove all existing StreamHandlers from this logger and the root logger
for handler in list(logger.handlers):
    if isinstance(handler, logging.StreamHandler):
        logger.removeHandler(handler)
        handler.close()
root_logger = logging.getLogger()
for handler in list(root_logger.handlers):
    if isinstance(handler, logging.StreamHandler):
        root_logger.removeHandler(handler)
        handler.close()

# Add only our custom QgsMessageLogHandler
if not any(isinstance(h, QgsMessageLogHandler) for h in logger.handlers):
    qgis_handler = QgsMessageLogHandler()
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    qgis_handler.setFormatter(formatter)
    logger.addHandler(qgis_handler)

logger.setLevel(logging.DEBUG)  # Set the overall logger level to DEBUG
logger.debug("pile_details_dialog logger initialized with QgsMessageLog.")

# 导入 Matplotlib
# 注意：这需要您的QGIS Python环境中已安装matplotlib
# 如果没有，请在OSGeo4W Shell中运行: python -m pip install matplotlib
import matplotlib

# Force matplotlib to use a non-interactive backend, to avoid issues with Qt
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

# Configure matplotlib to display Chinese characters
matplotlib.rcParams["font.sans-serif"] = [
    "SimHei",
    "Arial Unicode MS",
]  # Use SimHei or another installed Chinese font
matplotlib.rcParams["axes.unicode_minus"] = False  # Correctly display minus sign

# 导入数据库操作
from . import db_operations
from datetime import datetime, timedelta
import numpy as np

# 从 .ui 文件加载窗体类
FORM_CLASS, _ = uic.loadUiType(
    os.path.join(os.path.dirname(__file__), "pile_details_dialog.ui")
)


class PileDetailsDialog(QDialog, FORM_CLASS):
    """
    显示单个测试桩详细信息的对话框。
    """

    def __init__(self, feature, parent=None):
        """
        Constructor.
        :param feature: 被点击的测试桩要素 (QgsFeature)
        """
        super(PileDetailsDialog, self).__init__(parent)
        self.setupUi(self)
        self.feature = feature

        # --- 初始化变量 ---
        self.db_conn = None
        self.chart_canvas = None

        # --- 填充静态信息 ---
        self.populate_static_info()
        self.connect_signals()

        # --- 绘制电压历史曲线图 ---
        self.plot_voltage_history("24_hours")

    def connect_signals(self):
        """连接UI控件的信号到对应的槽函数"""
        self.past24HoursButton.clicked.connect(
            lambda: self.plot_voltage_history("24_hours")
        )
        self.pastMonthButton.clicked.connect(lambda: self.plot_voltage_history("month"))

    def populate_static_info(self):
        """用要素的属性填充UI上的标签"""
        # 从要素中获取属性值
        pile_name = self.feature.attribute("name") or "N/A"
        pile_id = self.feature.attribute("id") or "N/A"
        voltage = self.feature.attribute("voltage")
        risk_level = self.feature.attribute("risk_level") or "N/A"
        geom = self.feature.geometry()
        point = geom.asPoint()

        # 更新UI标签
        self.pileNameLabel.setText(f"<b>测试桩名称：</b> {pile_name} (ID: {pile_id})")
        self.coordinatesLabel.setText(
            f"<b>坐标：</b> Lng: {point.x():.6f}, Lat: {point.y():.6f}"
        )

        if voltage is not None:
            self.voltageLabel.setText(f"<b>当前电压：</b> {voltage:.3f} V")
        else:
            self.voltageLabel.setText("<b>当前电压：</b> N/A")

        self.riskLabel.setText(f"<b>风险评估：</b> {risk_level}")

    def _ensure_and_clear_chart_layout(self):
        """确保 chartContainer 有布局并清除其所有子部件"""
        current_layout = self.chartContainer.layout()
        if current_layout:
            # 如果已有布局，则移除所有子部件
            while current_layout.count():
                item = current_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
                elif item.layout() is not None:  # 如果是嵌套布局
                    self._clear_layout(item.layout())
        else:
            # 如果没有布局，则创建一个新的
            self.chartContainer.setLayout(QVBoxLayout())

    def plot_voltage_history(self, time_range="24_hours"):
        """
        获取并绘制测试桩的电压历史曲线。
        time_range: '24_hours' (过去24小时) 或 'month' (过去一个月，每天一个点)
        """
        self._ensure_and_clear_chart_layout()  # 确保布局存在并已清理

        if self.db_conn is None or not self.db_conn.is_connected():
            self.db_conn = db_operations.create_connection()
            if self.db_conn is None:
                logger.error("无法连接到数据库来获取历史数据。")
                # 如果无法连接数据库，也要显示"无数据"信息
                layout = self.chartContainer.layout()
                no_data_label = QLabel("无法连接到数据库。")
                no_data_label.setAlignment(Qt.AlignCenter)
                layout.addWidget(no_data_label)
                return

        pile_id = self.feature.attribute("id")
        readings = []

        if time_range == "24_hours":
            end_time = datetime.now()
            start_time = end_time - timedelta(hours=24)
            readings = db_operations.get_voltage_readings_for_pile(
                self.db_conn, pile_id, start_time, end_time
            )
            title_suffix = "过去24小时电压曲线"
            x_label = "时间"
        elif time_range == "month":
            end_time = datetime.now()
            start_time = end_time - timedelta(days=30)
            all_readings = db_operations.get_voltage_readings_for_pile(
                self.db_conn, pile_id, start_time, end_time
            )
            readings = self.process_daily_data(all_readings)  # 聚合每天的数据
            title_suffix = "过去一个月电压曲线"
            x_label = "日期"
        else:
            logger.error("不支持的时间范围。")
            return

        if not readings:
            self._ensure_and_clear_chart_layout()  # 清理布局并确保存在
            layout = self.chartContainer.layout()
            no_data_label = QLabel("无可用电压历史数据。")
            no_data_label.setAlignment(Qt.AlignCenter)
            layout.addWidget(no_data_label)
            logger.debug(f"测试桩 {pile_id} 没有历史数据。")
            return

        # Filter out 9999.0 placeholder for plotting
        filtered_readings = [r for r in readings if r["voltage"] != 9999.0]

        if not filtered_readings:
            self._ensure_and_clear_chart_layout()  # 清理布局并确保存在
            layout = self.chartContainer.layout()
            no_data_label = QLabel("无可用电压历史数据 (已过滤无效值)。")
            no_data_label.setAlignment(Qt.AlignCenter)
            layout.addWidget(no_data_label)
            logger.debug(f"测试桩 {pile_id} 过滤后没有有效历史数据。")
            return

        timestamps = [r["reading_timestamp"] for r in filtered_readings]
        voltages = [r["voltage"] for r in filtered_readings]

        # 创建 Matplotlib 图形
        fig = Figure(figsize=(5, 4), dpi=100)
        ax = fig.add_subplot(111)

        ax.plot(timestamps, voltages, marker="o", linestyle="-")
        ax.set_title(f"测试桩 {self.feature.attribute('name')} {title_suffix}")
        ax.set_xlabel(x_label)
        ax.set_ylabel("电压 (V)")
        ax.grid(True)
        fig.autofmt_xdate()  # 自动格式化日期标签

        # 将图表嵌入到 QWidget 中
        layout = self.chartContainer.layout()
        canvas = FigureCanvas(fig)
        layout.addWidget(canvas)

        logger.debug(f"成功绘制图表：{self.feature.attribute('name')} - {title_suffix}")

    def _clear_layout(self, layout):
        if layout is not None:
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
                elif item.layout() is not None:
                    self._clear_layout(item.layout())

    def process_daily_data(self, readings):
        """将一段时间内的读数按天聚合，计算每天的平均电压"""
        logger.debug(f"process_daily_data: 收到 {len(readings)} 条原始读数。")
        daily_data = {}
        for r in readings:
            # 过滤掉占位符数据
            if r["voltage"] == 9999.0:
                continue
            date_str = r["reading_timestamp"].strftime("%Y-%m-%d")
            if date_str not in daily_data:
                daily_data[date_str] = []
            daily_data[date_str].append(r["voltage"])

        aggregated_readings = []
        for date_str in sorted(daily_data.keys()):
            avg_voltage = np.mean(daily_data[date_str])
            # 使用每天的开始时间作为聚合点的时间戳
            aggregated_readings.append(
                {
                    "reading_timestamp": datetime.strptime(date_str, "%Y-%m-%d"),
                    "voltage": avg_voltage,
                }
            )
        logger.debug(
            f"process_daily_data: 聚合后得到 {len(aggregated_readings)} 条数据。"
        )
        return aggregated_readings

    def closeEvent(self, event):
        """确保在关闭对话框时断开数据库连接"""
        if self.db_conn and self.db_conn.is_connected():
            self.db_conn.close()
        super(PileDetailsDialog, self).closeEvent(event)
