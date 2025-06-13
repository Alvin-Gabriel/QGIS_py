# -*- coding: utf-8 -*-

import mysql.connector
from mysql.connector import Error
from datetime import datetime, timedelta

# 数据库连接配置
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
    except Error as e:
        print(f"连接数据库时发生错误: '{e}'")
    return connection


def get_pile_by_name(connection, name):
    """根据名称查询测试桩"""
    cursor = connection.cursor(dictionary=True)
    query = "SELECT id, name FROM test_piles WHERE name = %s"
    try:
        cursor.execute(query, (name,))
        pile = cursor.fetchone()
        return pile
    except Error as e:
        print(f"查询测试桩 '{name}' 时发生错误: '{e}'")
        return None
    finally:
        if cursor:
            cursor.close()


def get_all_test_piles(connection):
    """从 test_piles 表获取所有测试桩信息，并确保坐标为float类型"""
    cursor = connection.cursor(dictionary=True)
    query = "SELECT id, name, longitude, latitude, pipeline_id, description, created_at FROM test_piles ORDER BY id"
    try:
        cursor.execute(query)
        piles = cursor.fetchall()

        # 关键步骤：确保经纬度是float类型，而不是Decimal
        for pile in piles:
            pile["longitude"] = float(pile["longitude"])
            pile["latitude"] = float(pile["latitude"])

        return piles
    except Error as e:
        print(f"查询测试桩信息时发生错误: '{e}'")
        return []
    finally:
        if cursor:
            cursor.close()


def get_voltage_readings_for_pile(connection, pile_id, limit=365):
    """获取特定测试桩的电压读数"""
    cursor = connection.cursor(dictionary=True)
    query = "SELECT id, pile_id, voltage, reading_timestamp FROM voltage_readings WHERE pile_id = %s ORDER BY reading_timestamp DESC LIMIT %s"
    try:
        cursor.execute(query, (pile_id, limit))
        readings = cursor.fetchall()
        return readings
    except Error as e:
        print(f"查询测试桩 ID {pile_id} 的电压读数时发生错误: '{e}'")
        return []
    finally:
        if cursor:
            cursor.close()


def get_latest_voltages(connection):
    """获取每个测试桩的最新一条电压记录 (兼容旧版MySQL)"""
    cursor = connection.cursor(dictionary=True)
    query = """
    SELECT
        r.pile_id,
        r.voltage,
        r.reading_timestamp
    FROM
        voltage_readings r
    INNER JOIN (
        SELECT
            pile_id,
            MAX(reading_timestamp) AS max_timestamp
        FROM
            voltage_readings
        GROUP BY
            pile_id
    ) AS max_r
    ON
        r.pile_id = max_r.pile_id AND r.reading_timestamp = max_r.max_timestamp;
    """
    try:
        cursor.execute(query)
        latest_voltages = {row["pile_id"]: row for row in cursor.fetchall()}
        return latest_voltages
    except Error as e:
        print(f"查询最新电压数据时发生错误: '{e}'")
        return {}
    finally:
        if cursor:
            cursor.close()


# 注意：此文件是作为库被导入的，因此移除了 if __name__ == "__main__": 部分。
