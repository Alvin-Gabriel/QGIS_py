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
        if connection.is_connected():
            print(f"成功连接到数据库: {db_config['database']}")
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


def insert_test_pile_if_not_exists(
    connection, name, longitude, latitude, pipeline_id=None, description=None
):
    """
    如果测试桩不存在则插入，并返回其 ID。
    如果已存在，则直接返回其现有 ID。
    """
    existing_pile = get_pile_by_name(connection, name)
    if existing_pile:
        print(f"测试桩 '{name}' 已存在，ID 为: {existing_pile['id']}")
        return existing_pile["id"]
    else:
        cursor = connection.cursor()
        query = """
        INSERT INTO test_piles (name, longitude, latitude, pipeline_id, description)
        VALUES (%s, %s, %s, %s, %s)
        """
        try:
            cursor.execute(query, (name, longitude, latitude, pipeline_id, description))
            connection.commit()
            pile_id = cursor.lastrowid
            print(f"测试桩 '{name}' 成功插入，ID 为: {pile_id}")
            return pile_id
        except Error as e:
            print(f"插入测试桩 '{name}' 时发生错误: '{e}'")
            return None
        finally:
            if cursor:
                cursor.close()


def insert_voltage_reading(connection, pile_id, voltage, reading_timestamp_str):
    """向 voltage_readings 表插入一个新的电压读数"""
    cursor = connection.cursor()
    query = """
    INSERT INTO voltage_readings (pile_id, voltage, reading_timestamp)
    VALUES (%s, %s, %s)
    """
    try:
        reading_time = datetime.strptime(reading_timestamp_str, "%Y-%m-%d %H:%M:%S")
        cursor.execute(query, (pile_id, voltage, reading_time))
        connection.commit()
        reading_id = cursor.lastrowid
        print(
            f"为测试桩 ID {pile_id} 成功插入电压读数 {voltage}V，时间: {reading_timestamp_str}, 记录 ID: {reading_id}"
        )
        return reading_id
    except Error as e:
        print(f"为测试桩 ID {pile_id} 插入电压读数时发生错误: '{e}'")
        return None
    finally:
        if cursor:
            cursor.close()


def get_all_test_piles(connection):
    """从 test_piles 表获取所有测试桩信息"""
    cursor = connection.cursor(dictionary=True)
    query = "SELECT id, name, longitude, latitude, pipeline_id, description, created_at FROM test_piles"
    try:
        cursor.execute(query)
        piles = cursor.fetchall()
        print("\n---所有测试桩信息---")
        if piles:
            for pile in piles:
                print(pile)
        else:
            print("数据库中没有测试桩信息。")
        return piles
    except Error as e:
        print(f"查询测试桩信息时发生错误: '{e}'")
        return []
    finally:
        if cursor:
            cursor.close()


def get_voltage_readings_for_pile(connection, pile_id, limit=10):
    """获取特定测试桩的最新电压读数"""
    cursor = connection.cursor(dictionary=True)
    query = """
    SELECT id, pile_id, voltage, reading_timestamp
    FROM voltage_readings
    WHERE pile_id = %s
    ORDER BY reading_timestamp DESC
    LIMIT %s
    """
    try:
        cursor.execute(query, (pile_id, limit))
        readings = cursor.fetchall()
        print(f"\n---测试桩 ID {pile_id} 的最新 {limit} 条电压读数---")
        if readings:
            for reading in readings:
                print(reading)
        else:
            print(f"测试桩 ID {pile_id} 没有电压读数记录。")
        return readings
    except Error as e:
        print(f"查询测试桩 ID {pile_id} 的电压读数时发生错误: '{e}'")
        return []
    finally:
        if cursor:
            cursor.close()


# --- 主程序执行部分 ---
if __name__ == "__main__":
    conn = create_connection()

    if conn and conn.is_connected():
        print("\n---开始处理测试桩并插入电压数据---")
        # 1. 尝试插入或获取测试桩 ID
        pile1_id = insert_test_pile_if_not_exists(
            conn,
            "Python测试桩C003",
            116.420000,
            39.910000,
            "管线P02",
            "通过Python脚本处理",
        )
        pile2_id = insert_test_pile_if_not_exists(
            conn,
            "Python测试桩D004",
            116.425000,
            39.912000,
            "管线P02",
            "另一个Python脚本处理的桩",
        )
        # (可选) 获取手动插入的桩的ID，如果需要为其添加电压数据
        # manual_pile_A001 = get_pile_by_name(conn, '测试桩A001')
        # manual_pile_B002 = get_pile_by_name(conn, '测试桩B002')

        # 2. 为这些测试桩插入一些新的电压读数
        if pile1_id:  # 确保 pile1_id 不是 None
            insert_voltage_reading(
                conn, pile1_id, -0.760, datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            )  # 插入新数据
            insert_voltage_reading(
                conn,
                pile1_id,
                -0.765,
                (datetime.now() - timedelta(seconds=30)).strftime("%Y-%m-%d %H:%M:%S"),
            )

        if pile2_id:  # 确保 pile2_id 不是 None
            now = datetime.now()
            insert_voltage_reading(
                conn,
                pile2_id,
                -0.910,
                (now - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S"),
            )  # 插入新数据
            insert_voltage_reading(
                conn, pile2_id, -0.915, now.strftime("%Y-%m-%d %H:%M:%S")
            )

        # 3. 查询并显示所有测试桩的信息
        all_piles_data = get_all_test_piles(conn)

        # 4. 查询并显示特定测试桩的电压读数
        if pile1_id:  # 使用从 insert_test_pile_if_not_exists 获取的 ID
            get_voltage_readings_for_pile(conn, pile1_id, limit=5)

        # 如果想查询手动添加的桩的数据
        # if manual_pile_A001:
        #    get_voltage_readings_for_pile(conn, manual_pile_A001['id'])

        # 关闭数据库连接
        if conn.is_connected():
            conn.close()
            print("\n数据库连接已关闭。")
