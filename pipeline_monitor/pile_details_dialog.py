# -*- coding: utf-8 -*-

import os
from qgis.PyQt import uic
from qgis.PyQt.QtWidgets import QDialog, QVBoxLayout

# 导入 Matplotlib
# 注意：这需要您的QGIS Python环境中已安装matplotlib
# 如果没有，请在OSGeo4W Shell中运行: python -m pip install matplotlib
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

# 导入数据库操作
from . import db_operations

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

        # --- 绘制电压历史曲线图 ---
        self.plot_voltage_history()

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

    def plot_voltage_history(self):
        """查询历史数据并用Matplotlib绘制图表"""
        pile_id = self.feature.attribute("id")
        if pile_id is None:
            return

        self.db_conn = db_operations.create_connection()
        if not (self.db_conn and self.db_conn.is_connected()):
            print("绘制图表错误：无法连接到数据库。")
            return

        # 查询特定测试桩的所有历史电压数据
        # 注意：您需要在 db_operations.py 中添加这个函数
        history_data = db_operations.get_voltage_readings_for_pile(
            self.db_conn, pile_id, limit=365
        )  # 获取最近一年的数据
        self.db_conn.close()

        if not history_data:
            self.chartContainer.setLayout(QVBoxLayout())
            self.chartContainer.layout().addWidget(plt.QLabel("无历史电压数据。"))
            return

        # 准备绘图数据
        history_data.reverse()  # 反转列表，让时间从左到右递增
        dates = [row["reading_timestamp"] for row in history_data]
        voltages = [row["voltage"] for row in history_data]

        # 创建一个Matplotlib图形并嵌入到UI中
        # 创建一个FigureCanvas
        self.chart_canvas = FigureCanvas(Figure(figsize=(8, 4)))
        # 在UI的chartContainer中设置一个布局
        layout = QVBoxLayout()
        self.chartContainer.setLayout(layout)
        # 将FigureCanvas添加到布局中
        layout.addWidget(self.chart_canvas)

        # 绘图
        ax = self.chart_canvas.figure.subplots()
        ax.plot(dates, voltages, marker="o", linestyle="-", markersize=4)
        ax.set_title(f"测试桩 {pile_id} 电压历史曲线")
        ax.set_xlabel("时间")
        ax.set_ylabel("电压 (V)")
        ax.grid(True)
        self.chart_canvas.figure.tight_layout()  # 调整布局以防止标签重叠
        self.chart_canvas.draw()

    def closeEvent(self, event):
        """确保在关闭对话框时断开数据库连接"""
        if self.db_conn and self.db_conn.is_connected():
            self.db_conn.close()
        super(PileDetailsDialog, self).closeEvent(event)
