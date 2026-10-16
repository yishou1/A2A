"""
消融实验主脚本
用于启动完整的概率化MROAW + Agentic PPR消融实验
"""

import os
import sys
import argparse
from datetime import datetime
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.experiments.exp_configs import (
    ABLATION_CONFIGS, DATASETS, COMMON_CONFIG, print_experiment_matrix
)
from scripts.experiments.parallel_runner import ParallelExperimentRunner


def check_environment():
    """检查实验环境"""
    print("\n🔍 环境检查...")

    issues = []

    # 检查CUDA
    import subprocess
    try:
        result = subprocess.run(['nvidia-smi'], capture_output=True, text=True)
        if result.returncode != 0:
            issues.append("❌ CUDA不可用")
        else:
            print("  ✅ CUDA可用")
    except FileNotFoundError:
        issues.append("❌ nvidia-smi命令未找到")

    # 检查API Key
    api_key = os.environ.get('SYNAPSERAG_OPENAI_API_KEY')
    if not api_key:
        issues.append("⚠️  SYNAPSERAG_OPENAI_API_KEY 环境变量未设置")
    else:
        print(f"  ✅ API Key已设置 (前8位: {api_key[:8]}...)")

    # 检查数据集
    dataset_dir = Path('reproduce/dataset')
    for dataset in DATASETS:
        corpus_file = dataset_dir / f"{dataset}_corpus.json"
        if not corpus_file.exists():
            issues.append(f"❌ 数据集缺失: {corpus_file}")
        else:
            print(f"  ✅ 数据集存在: {dataset}")

    # 检查输出目录
    output_dir = Path('experiments/ablation_mroaw_agentic')
    if not output_dir.exists():
        print(f"  ⚠️  输出目录不存在，将自动创建: {output_dir}")
        output_dir.mkdir(parents=True, exist_ok=True)
    else:
        print(f"  ✅ 输出目录存在: {output_dir}")

    if issues:
        print("\n⚠️  发现以下问题:")
        for issue in issues:
            print(f"  {issue}")
        print("\n请修复后再运行实验\n")
        return False

    print("\n✅ 环境检查通过\n")
    return True


def print_experiment_plan(configs, datasets, gpu_ids):
    """打印实验计划"""
    print("\n" + "="*80)
    print("📋 实验计划")
    print("="*80)

    print(f"\n🔧 配置信息:")
    print(f"  LLM: {COMMON_CONFIG['llm_name']}")
    print(f"  嵌入: {COMMON_CONFIG['embedding_name']}")
    print(f"  Base URL: {COMMON_CONFIG['llm_base_url']}")

    print(f"\n💻 计算资源:")
    print(f"  GPU数量: {len(gpu_ids)}")
    print(f"  GPU IDs: {gpu_ids}")

    print(f"\n🧪 实验配置 ({len(configs)}个):")
    for i, config_name in enumerate(configs, 1):
        config = ABLATION_CONFIGS[config_name]
        print(f"  {i}. {config['short_name']:20s} - {config['description']}")

    print(f"\n📊 数据集 ({len(datasets)}个):")
    for dataset in datasets:
        print(f"  - {dataset}")

    total_experiments = len(configs) * len(datasets)
    print(f"\n📈 总实验数: {len(configs)} × {len(datasets)} = {total_experiments}")

    # 时间估算
    avg_time_per_exp = 40  # 分钟（保守估计）
    total_time = (total_experiments * avg_time_per_exp) / len(gpu_ids)
    print(f"⏱️  预计耗时: {total_time/60:.1f} 小时 (假设{avg_time_per_exp}分钟/实验)")

    print("="*80 + "\n")


def confirm_execution():
    """确认是否执行"""
    response = input("🚀 是否开始执行实验？ (yes/no): ").strip().lower()
    return response in ['yes', 'y', '是']


def main():
    parser = argparse.ArgumentParser(
        description='概率化MROAW + Agentic PPR 消融实验',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法:

1. 完整实验（6配置 × 3数据集，使用4张GPU）:
   python scripts/experiments/run_ablation_study.py --gpus 0,1,2,3

2. 快速测试（仅baseline，仅musique，使用1张GPU）:
   python scripts/experiments/run_ablation_study.py \\
       --configs baseline \\
       --datasets musique \\
       --gpus 0

3. 核心对比（4个关键配置）:
   python scripts/experiments/run_ablation_study.py \\
       --configs baseline,mroaw_prob_only,agentic_only,full_fusion \\
       --gpus 0,1,2,3

4. 跳过确认直接运行:
   python scripts/experiments/run_ablation_study.py --gpus 0,1,2,3 --yes
        """
    )

    parser.add_argument(
        '--gpus',
        type=str,
        default='0,1,2,3',
        help='GPU IDs (逗号分隔), 默认: 0,1,2,3'
    )
    parser.add_argument(
        '--configs',
        type=str,
        default=None,
        help='配置列表 (逗号分隔), 默认全部。可选: ' + ', '.join(ABLATION_CONFIGS.keys())
    )
    parser.add_argument(
        '--datasets',
        type=str,
        default=None,
        help='数据集列表 (逗号分隔), 默认全部。可选: ' + ', '.join(DATASETS)
    )
    parser.add_argument(
        '--skip-check',
        action='store_true',
        help='跳过环境检查'
    )
    parser.add_argument(
        '--yes',
        action='store_true',
        help='跳过确认，直接执行'
    )

    args = parser.parse_args()

    # 打印标题
    print("\n" + "="*80)
    print(" " * 20 + "概率化MROAW + Agentic PPR 消融实验")
    print(" " * 30 + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("="*80)

    # 环境检查
    if not args.skip_check:
        if not check_environment():
            sys.exit(1)

    # 解析参数
    gpu_ids = [int(x.strip()) for x in args.gpus.split(',')]

    configs = None
    if args.configs:
        configs = [x.strip() for x in args.configs.split(',')]
        # 验证配置
        invalid = set(configs) - set(ABLATION_CONFIGS.keys())
        if invalid:
            print(f"❌ 无效配置: {invalid}")
            print(f"   可用配置: {list(ABLATION_CONFIGS.keys())}")
            sys.exit(1)
    else:
        configs = list(ABLATION_CONFIGS.keys())

    datasets = None
    if args.datasets:
        datasets = [x.strip() for x in args.datasets.split(',')]
    else:
        datasets = DATASETS

    # 打印实验计划
    print_experiment_plan(configs, datasets, gpu_ids)

    # 确认执行
    if not args.yes:
        if not confirm_execution():
            print("\n❌ 实验已取消\n")
            sys.exit(0)

    # 创建运行器并执行
    print("\n" + "="*80)
    print("🚀 开始执行实验...")
    print("="*80 + "\n")

    runner = ParallelExperimentRunner(gpu_ids, configs, datasets)

    import time
    start_time = time.time()

    try:
        results = runner.run()
        total_time = time.time() - start_time

        # 打印总结
        runner.print_summary(results)

        # 保存元数据
        import json
        metadata_file = (
            f"experiments/ablation_mroaw_agentic/"
            f"metadata_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )
        with open(metadata_file, 'w') as f:
            json.dump({
                'timestamp': datetime.now().isoformat(),
                'total_time_hours': total_time / 3600,
                'gpu_ids': gpu_ids,
                'configs': configs,
                'datasets': datasets,
                'common_config': COMMON_CONFIG,
                'results': results
            }, f, indent=2, ensure_ascii=False)

        print(f"📄 元数据已保存: {metadata_file}")

        # 提示下一步
        print("\n" + "="*80)
        print("✅ 实验执行完成！")
        print("="*80)
        print("\n下一步操作:")
        print("  1. 查看实验日志:")
        print("     ls -lh experiments/ablation_mroaw_agentic/logs/")
        print("\n  2. 聚合实验结果:")
        print("     python scripts/analysis/aggregate_results.py")
        print("\n  3. 生成可视化:")
        print("     python scripts/analysis/visualize_ablation.py")
        print("\n  4. 生成LaTeX表格:")
        print("     python scripts/analysis/generate_tables.py")
        print("\n")

    except KeyboardInterrupt:
        print("\n\n⚠️  实验被用户中断")
        print("部分实验可能已完成，请查看日志目录\n")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ 实验执行出错: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
