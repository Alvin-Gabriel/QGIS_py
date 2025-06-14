import mysql.connector
from mysql.connector import Error
from datetime import datetime, timedelta
import random
import logging  # Import logging

# Initialize logger for this module
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)  # Set to DEBUG for development, INFO for general use

# 数据库连接配置 (从 db_operations.py 复制)
db_config = {
    "host": "localhost",
    "user": "root",
    "password": "Alexlsy",
    "database": "pipeline_monitoring_db",
}


def create_connection():
    """创建并返回一个数据库连接"""
    connection = None
    try:
        connection = mysql.connector.connect(**db_config)
        if connection.is_connected():
            logger.info("成功连接到 MySQL 数据库。")  # Replaced print
    except Error as e:
        logger.error(f"连接数据库时发生错误: '{e}'")  # Replaced print
    return connection


def insert_voltage_reading(connection, pile_id, voltage, timestamp):
    """向 voltage_readings 表插入一条电压读数记录"""
    cursor = connection.cursor()
    # 假设 'id' 是 AUTO_INCREMENT，因此不包含在 INSERT 语句中
    query = "INSERT INTO voltage_readings (pile_id, voltage, reading_timestamp) VALUES (%s, %s, %s)"
    try:
        cursor.execute(query, (pile_id, voltage, timestamp))
        connection.commit()
        # logger.debug(f"已插入桩 ID {pile_id} 在 {timestamp} 的读数，电压为 {voltage}") # Replaced print, changed to debug
    except Error as e:
        logger.error(f"插入电压读数时发生错误: '{e}'")  # Replaced print
        connection.rollback()
    finally:
        if cursor:
            cursor.close()


def generate_and_insert_data():
    """生成并插入模拟数据"""
    conn = create_connection()
    if conn is None:
        logger.error("无法连接到数据库。无法生成数据。")  # Replaced print
        return

    # 定义测试桩 ID 及其期望的特性
    piles_data = {
        1: {
            "name": "测试桩1",
            "state": "过保护",
            "base_voltage": -1.3,
        },  # 过保护: < -1.2V
        2: {
            "name": "测试桩2",
            "state": "正常",
            "base_voltage": -1.0,
        },  # 正常: -1.2V <= x <= -0.85V
        3: {
            "name": "测试桩3",
            "state": "欠保护",
            "base_voltage": -0.7,
        },  # 欠保护: > -0.85V
        4: {"name": "测试桩4", "state": "未知", "base_voltage": None},  # 未知: None
    }

    logger.info("正在生成并插入模拟数据...")  # Replaced print

    for pile_id, data in piles_data.items():
        base_voltage = data["base_voltage"]

        # 1. 生成过去 24 小时的数据 (每小时一个点)
        logger.info(
            f"正在为 {data['name']} (ID: {pile_id}) 生成 24 小时数据..."
        )  # Replaced print
        for i in range(24):
            timestamp = datetime.now() - timedelta(
                hours=i, minutes=random.randint(0, 59)
            )
            if base_voltage is not None:
                # 在基础电压上添加一些随机性
                voltage = base_voltage + random.uniform(-0.05, 0.05)
                # 确保电压值在合理范围内以符合其状态
                if data["state"] == "过保护":
                    voltage = min(voltage, -1.25)  # 确保低于过保护阈值
                elif data["state"] == "正常":
                    voltage = max(-1.15, min(voltage, -0.9))  # 确保在正常范围内
                elif data["state"] == "欠保护":
                    voltage = max(voltage, -0.8)  # 确保高于欠保护阈值
            else:
                voltage = 9999.0  # 未知状态，使用一个占位符，因为列不允许为 NULL
            insert_voltage_reading(conn, pile_id, voltage, timestamp)

        # 2. 生成过去一个月的数据 (每天一个点)
        logger.info(
            f"正在为 {data['name']} (ID: {pile_id}) 生成 1 个月数据..."
        )  # Replaced print
        for i in range(30):
            timestamp = datetime.now() - timedelta(
                days=i, hours=random.randint(0, 23), minutes=random.randint(0, 59)
            )
            if base_voltage is not None:
                voltage = base_voltage + random.uniform(-0.1, 0.1)
                # 应用与 24 小时数据相似的边界
                if data["state"] == "过保护":
                    voltage = min(voltage, -1.25)
                elif data["state"] == "正常":
                    voltage = max(-1.15, min(voltage, -0.9))
                elif data["state"] == "欠保护":
                    voltage = max(voltage, -0.8)
            else:
                voltage = 9999.0  # 未知状态，使用一个占位符
            insert_voltage_reading(conn, pile_id, voltage, timestamp)

    conn.close()
    logger.info("模拟数据生成完成。")  # Replaced print


if __name__ == "__main__":
    generate_and_insert_data()
