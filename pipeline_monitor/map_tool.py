# -*- coding: utf-8 -*-

from qgis.gui import QgsMapToolEmitPoint


class PointTool(QgsMapToolEmitPoint):
    """
    一个简单的地图工具，只负责在用户点击地图时，
    发射一个包含点击坐标的内置信号 (canvasClicked)。
    """

    def __init__(self, canvas):
        """Constructor."""
        super(PointTool, self).__init__(canvas)
        print("DEBUG: PointTool (更简单的工具) 已创建。")
