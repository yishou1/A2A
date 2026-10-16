#!/usr/bin/env python3
"""
验证消融实验环境配置
检查所有必要的文件、参数支持和配置是否正确
"""

import os
import sys
import subprocess
from pathlib import Path

# 切换到项目根目录
project_root = Path(__file__).parent.parent.parent
os.chdir(project_root)
sys.path.insert(0, str(project_root))

from scripts.experiments.exp_configs import ABLATION_CONFIGS, DATASETS, get_config_command


def test_main_ablation_exists():
    """测试 main_ablation.py 是否存在"""
    main_ablation = Path("main_ablation.py")
    assert main_ablation.exists(), "❌ main_ablation.py 不存在"
    print("✅ main_ablation.py 存在")
    return True


def test_main_ablation_parameters():
    """测试 main_ablation.py 是否支持所有必需参数"""
    result = subprocess.run(
        ['python', 'main_ablation.py', '--help'],
        capture_output=True,
        text=True
    )

    help_text = result.stdout

    required_params = [
        '--edge_weight_mode',
        '--use_agentic_ppr_reset',
        '--mroaw_use_info_likelihood',
        '--mroaw_use_struct_likelihood',
        '--mroaw_use_semantic_likelihood'
    ]

    missing_params = []
    for param in required_params:
        if param not in help_text:
            missing_params.append(param)

    if missing_params:
        print(f"❌ main_ablation.py 缺少参数: {missing_params}")
        return False

    print("✅ main_ablation.py 支持所有必需参数")
    return True


def test_datasets_exist():
    """测试数据集文件是否存在"""
    dataset_dir = Path("reproduce/dataset")
    missing_datasets = []

    for dataset in DATASETS:
        corpus_file = dataset_dir / f"{dataset}_corpus.json"
        data_file = dataset_dir / f"{dataset}.json"

        if not corpus_file.exists():
            missing_datasets.append(str(corpus_file))
        if not data_file.exists():
            missing_datasets.append(str(data_file))

    if missing_datasets:
        print(f"⚠️  缺少数据集文件:")
        for f in missing_datasets:
            print(f"   - {f}")
        return False

    print(f"✅ 所有 {len(DATASETS)} 个数据集文件存在")
    return True


def test_config_generation():
    """测试配置命令生成"""
    print("\n📝 测试配置命令生成:")

    all_valid = True
    for config_name in ABLATION_CONFIGS.keys():
        try:
            cmd = get_config_command(config_name, 'musique')

            # 检查关键参数是否在命令中
            config = ABLATION_CONFIGS[config_name]

            if f"--edge_weight_mode {config['edge_weight_mode']}" not in cmd:
                print(f"  ❌ {config_name}: edge_weight_mode 参数错误")
                all_valid = False
                continue

            print(f"  ✅ {config_name}: 命令生成正确")

        except Exception as e:
            print(f"  ❌ {config_name}: 生成失败 - {e}")
            all_valid = False

    return all_valid


def test_log_directory():
    """测试日志目录"""
    log_dir = Path("experiments/ablation_mroaw_agentic/logs")

    if not log_dir.exists():
        print("⚠️  日志目录不存在，正在创建...")
        log_dir.mkdir(parents=True, exist_ok=True)
        print(f"✅ 创建日志目录: {log_dir}")
    else:
        print(f"✅ 日志目录存在: {log_dir}")

    return True


def test_api_key():
    """测试 API Key 是否设置"""
    api_key = os.environ.get('SYNAPSERAG_OPENAI_API_KEY')

    if not api_key:
        print("⚠️  SYNAPSERAG_OPENAI_API_KEY 环境变量未设置")
        print("   请运行: export SYNAPSERAG_OPENAI_API_KEY=<your-key>")
        return False

    print(f"✅ API Key 已设置 (前8位: {api_key[:8]}...)")
    return True


def test_cuda():
    """测试 CUDA 是否可用"""
    try:
        result = subprocess.run(
            ['nvidia-smi'],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode != 0:
            print("❌ CUDA 不可用")
            return False

        # 解析 GPU 信息
        lines = result.stdout.split('\n')
        gpu_count = sum(1 for line in lines if '|' in line and 'MiB' in line and 'Default' not in line)

        print(f"✅ CUDA 可用 (检测到 {gpu_count} 个 GPU)")
        return True

    except FileNotFoundError:
        print("❌ nvidia-smi 命令未找到")
        return False
    except Exception as e:
        print(f"❌ CUDA 检查失败: {e}")
        return False


def print_experiment_summary():
    """打印实验概览"""
    print("\n" + "="*80)
    print("📊 实验配置概览")
    print("="*80)
    print(f"配置数量: {len(ABLATION_CONFIGS)}")
    print(f"数据集数量: {len(DATASETS)}")
    print(f"总实验数: {len(ABLATION_CONFIGS) * len(DATASETS)}")
    print("\n配置列表:")
    for i, (name, config) in enumerate(ABLATION_CONFIGS.items(), 1):
        print(f"  {i}. {config['short_name']:20s} - {config['description']}")
    print("\n数据集列表:")
    for dataset in DATASETS:
        print(f"  - {dataset}")
    print("="*80)


def main():
    """运行所有测试"""
    print("\n" + "="*80)
    print("🧪 消融实验环境配置测试")
    print("="*80 + "\n")

    tests = [
        ("main_ablation.py 存在性", test_main_ablation_exists),
        ("main_ablation.py 参数支持", test_main_ablation_parameters),
        ("数据集文件", test_datasets_exist),
        ("配置命令生成", test_config_generation),
        ("日志目录", test_log_directory),
        ("API Key", test_api_key),
        ("CUDA 可用性", test_cuda),
    ]

    results = {}
    for test_name, test_func in tests:
        try:
            results[test_name] = test_func()
        except Exception as e:
            print(f"❌ {test_name}: 测试失败 - {e}")
            results[test_name] = False
        print()

    # 打印总结
    print("="*80)
    print("📊 测试总结")
    print("="*80)

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for test_name, passed_test in results.items():
        status = "✅ 通过" if passed_test else "❌ 失败"
        print(f"  {status:10s} - {test_name}")

    print("\n" + "="*80)
    print(f"结果: {passed}/{total} 测试通过")
    print("="*80 + "\n")

    # 打印实验概览
    print_experiment_summary()

    # 提示下一步
    if passed == total:
        print("\n✅ 所有测试通过！可以开始运行实验了。\n")
        print("运行命令:")
        print("  python scripts/experiments/run_ablation_study.py --gpus 0,1,2,3\n")
        return 0
    else:
        print("\n⚠️  部分测试未通过，请修复后再运行实验。\n")
        return 1


if __name__ == '__main__':
    sys.exit(main())
