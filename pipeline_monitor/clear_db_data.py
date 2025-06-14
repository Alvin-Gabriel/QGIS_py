import mysql.connector
from mysql.connector import Error
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


def clear_voltage_readings_table():
    """清空 voltage_readings 表中的所有数据"""
    conn = create_connection()
    if conn is None:
        logger.error("无法连接到数据库。无法清空数据。")  # Replaced print
        return

    cursor = conn.cursor()
    try:
        query = "DELETE FROM voltage_readings"
        cursor.execute(query)
        conn.commit()
        logger.info("成功清空 voltage_readings 表中的数据。")  # Replaced print
    except Error as e:
        logger.error(f"清空数据时发生错误: '{e}'")  # Replaced print
        conn.rollback()
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


if __name__ == "__main__":
    clear_voltage_readings_table()
