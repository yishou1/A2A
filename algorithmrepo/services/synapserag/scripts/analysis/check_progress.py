"""
实时监控实验进度
检查已完成的实验和当前运行状态
"""

import sys
import os
from pathlib import Path
from datetime import datetime
import json
import time

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.experiments.exp_configs import ABLATION_CONFIGS, DATASETS


def check_experiment_status(output_dir='outputs', log_dir='experiments/ablation_mroaw_agentic/logs'):
    """
    检查实验完成状态

    Returns:
        dict: 状态统计
    """
    output_path = Path(output_dir)
    log_path = Path(log_dir)

    status_matrix = {}
    total = len(ABLATION_CONFIGS) * len(DATASETS)
    completed = 0
    running = 0
    pending = 0

    for config_name in ABLATION_CONFIGS.keys():
        for dataset in DATASETS:
            key = (config_name, dataset)

            # 检查是否有输出结果
            has_output = False
            if output_path.exists():
                # 查找对应的实验结果JSON
                dataset_dir = output_path / dataset
                if dataset_dir.exists():
                    for model_dir in dataset_dir.iterdir():
                        if not model_dir.is_dir():
                            continue

                        exp_dir = model_dir / 'experiments'
                        if exp_dir.exists():
                            for json_file in exp_dir.glob('exp_*.json'):
                                with open(json_file, 'r') as f:
                                    data = json.load(f)
                                    config_info = data.get('config', {})

                                    # 检查是否匹配当前配置
                                    if (config_info.get('edge_weight_mode') == ABLATION_CONFIGS[config_name]['edge_weight_mode'] and
                                        config_info.get('use_agentic_ppr_reset') == ABLATION_CONFIGS[config_name].get('use_agentic_ppr_reset', False)):
                                        has_output = True
                                        break

                        if has_output:
                            break

            # 检查是否有正在运行的日志
            is_running = False
            latest_log = None
            if log_path.exists():
                # 查找最新的日志文件
                log_pattern = f"exp_{config_name}_{dataset}_*.log"
                matching_logs = sorted(log_path.glob(log_pattern), key=lambda x: x.stat().st_mtime, reverse=True)

                if matching_logs:
                    latest_log = matching_logs[0]
                    # 检查日志最后修改时间（如果5分钟内有更新，认为正在运行）
                    last_modified = latest_log.stat().st_mtime
                    if time.time() - last_modified < 300:  # 5分钟
                        is_running = True

            # 确定状态
            if has_output:
                status = '✅ Done'
                completed += 1
            elif is_running:
                status = '🔄 Running'
                running += 1
            else:
                status = '⏳ Pending'
                pending += 1

            status_matrix[key] = {
                'status': status,
                'log': latest_log.name if latest_log else None
            }

    return {
        'matrix': status_matrix,
        'total': total,
        'completed': completed,
        'running': running,
        'pending': pending
    }


def print_status_table(status_info):
    """打印状态表"""
    matrix = status_info['matrix']

    print("\n" + "="*120)
    print("📊 实验进度监控")
    print("="*120)

    # 表头
    header = f"{'配置':<25s} | "
    for dataset in DATASETS:
        header += f"{dataset:<20s} | "
    print(header)
    print("-"*120)

    # 数据行
    for config_name in ABLATION_CONFIGS.keys():
        config = ABLATION_CONFIGS[config_name]
        row = f"{config['short_name']:<25s} | "

        for dataset in DATASETS:
            key = (config_name, dataset)
            status = matrix[key]['status']
            row += f"{status:<20s} | "

        print(row)

    print("="*120)

    # 统计信息
    print(f"\n📈 总体进度: {status_info['completed']}/{status_info['total']} 完成")
    print(f"   ✅ 已完成: {status_info['completed']}")
    print(f"   🔄 运行中: {status_info['running']}")
    print(f"   ⏳ 待执行: {status_info['pending']}")

    completion_rate = status_info['completed'] / status_info['total'] * 100
    print(f"   📊 完成率: {completion_rate:.1f}%")

    # 预估剩余时间（假设每个实验40分钟）
    remaining = status_info['pending'] + status_info['running']
    if remaining > 0:
        # 假设有4张GPU并行
        estimated_hours = (remaining * 40) / (4 * 60)
        print(f"   ⏱️  预估剩余: {estimated_hours:.1f} 小时")

    print("\n")


def print_detailed_status(status_info):
    """打印详细状态（包括日志文件）"""
    matrix = status_info['matrix']

    print("📋 详细状态:")
    print("-"*120)

    for config_name in ABLATION_CONFIGS.keys():
        config = ABLATION_CONFIGS[config_name]
        print(f"\n[{config['short_name']}]")

        for dataset in DATASETS:
            key = (config_name, dataset)
            info = matrix[key]
            print(f"  {dataset:<20s}: {info['status']:<15s}", end='')

            if info['log']:
                print(f"  日志: {info['log']}")
            else:
                print()

    print("-"*120 + "\n")


def watch_mode(interval=30):
    """
    监控模式：每隔一段时间刷新状态

    Args:
        interval: 刷新间隔（秒）
    """
    print("\n🔍 启动监控模式（按Ctrl+C退出）")
    print(f"   刷新间隔: {interval}秒\n")

    try:
        while True:
            # 清屏（Unix/Linux/Mac）
            if os.name != 'nt':
                os.system('clear')
            else:
                os.system('cls')

            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")

            status_info = check_experiment_status()
            print_status_table(status_info)

            if status_info['completed'] == status_info['total']:
                print("✅ 所有实验已完成！\n")
                break

            time.sleep(interval)

    except KeyboardInterrupt:
        print("\n\n⚠️  监控已停止\n")


def main():
    import argparse

    parser = argparse.ArgumentParser(description='监控实验进度')
    parser.add_argument(
        '--output-dir',
        default='outputs',
        help='实验输出目录，默认: outputs'
    )
    parser.add_argument(
        '--log-dir',
        default='experiments/ablation_mroaw_agentic/logs',
        help='日志目录'
    )
    parser.add_argument(
        '--watch',
        action='store_true',
        help='启动监控模式（自动刷新）'
    )
    parser.add_argument(
        '--interval',
        type=int,
        default=30,
        help='监控模式刷新间隔（秒），默认: 30'
    )
    parser.add_argument(
        '--detailed',
        action='store_true',
        help='显示详细状态（包括日志文件）'
    )

    args = parser.parse_args()

    if args.watch:
        watch_mode(args.interval)
    else:
        status_info = check_experiment_status(args.output_dir, args.log_dir)
        print_status_table(status_info)

        if args.detailed:
            print_detailed_status(status_info)

        if status_info['completed'] == status_info['total']:
            print("✅ 所有实验已完成！")
            print("\n下一步操作:")
            print("  python scripts/analysis/aggregate_results.py")
            print("\n")


if __name__ == '__main__':
    main()
