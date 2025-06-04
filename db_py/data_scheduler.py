import schedule
import time
import random
from datetime import datetime, timedelta

from db_operations import (
    create_connection,
    get_all_test_piles,
    insert_voltage_reading,
)


def generate_and_insert_new_readings():
    """模拟生成新的电压读数并插入数据库"""
    print(f"[{datetime.now()}] 检查并插入新的电压读数...")
    conn = create_connection()
    if not (conn and conn.is_connected()):
        print("无法连接到数据库，跳过此次数据生成。")
        return

    try:
        # 获取所有测试桩，或者选择一部分进行模拟
        piles = get_all_test_piles(conn)
        if not piles:
            print("没有找到测试桩信息，无法生成电压数据。")
            return

        # 随机选择1到3个桩为其生成数据
        num_piles_to_update = random.randint(1, min(len(piles), 3))
        selected_piles = random.sample(piles, num_piles_to_update)

        for pile in selected_piles:
            pile_id = pile["id"]
            # 模拟电压值 (在 -0.5V 到 -1.5V 之间)
            simulated_voltage = round(random.uniform(-1.5, -0.5), 3)
            current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            insert_voltage_reading(conn, pile_id, simulated_voltage, current_time_str)

    except Exception as e:
        print(f"生成并插入电压数据时发生错误: {e}")
    finally:
        if conn and conn.is_connected():
            conn.close()
            print(f"[{datetime.now()}] 电压读数检查完毕，数据库连接已关闭。")


# --- 定时任务设置 ---
if __name__ == "__main__":
    print("定时数据生成程序已启动。按 Ctrl+C 退出。")

    # 每分钟执行一次 generate_and_insert_new_readings 函数
    schedule.every(10).second.do(generate_and_insert_new_readings)

    try:
        while True:
            schedule.run_pending()  # 运行所有已到时间的任务
            time.sleep(1)  # 等待1秒钟，避免CPU占用过高
    except KeyboardInterrupt:
        print("定时数据生成程序已停止。")
