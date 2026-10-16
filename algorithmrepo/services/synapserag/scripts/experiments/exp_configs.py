"""
实验配置定义
用于概率化MROAW + Agentic PPR消融实验
"""

# 6种消融实验配置
ABLATION_CONFIGS = {
    'baseline': {
        'edge_weight_mode': 'uniform',
        'use_agentic_ppr_reset': False,
        'description': 'HippoRAG原始方法（无MROAW，无Agentic）',
        'short_name': 'Baseline'
    },
    'mroaw_prob_only': {
        'edge_weight_mode': 'probabilistic_mroaw',
        'use_agentic_ppr_reset': False,
        'mroaw_use_info_likelihood': True,
        'mroaw_use_struct_likelihood': True,
        'mroaw_use_semantic_likelihood': True,
        'description': '仅概率化MROAW（创新点1）',
        'short_name': 'MROAW-Prob'
    },
    'agentic_only': {
        'edge_weight_mode': 'base',
        'use_agentic_ppr_reset': True,
        'description': '仅Agentic PPR（创新点2）',
        'short_name': 'Agentic'
    },
    'mroaw_heur_only': {
        'edge_weight_mode': 'heuristic_mroaw',
        'use_agentic_ppr_reset': False,
        'description': '启发式MROAW（对比基线）',
        'short_name': 'MROAW-Heur'
    },
    'mroaw_heur_agentic': {
        'edge_weight_mode': 'heuristic_mroaw',
        'use_agentic_ppr_reset': True,
        'description': '启发式MROAW + Agentic',
        'short_name': 'Heur+Agentic'
    },
    'full_fusion': {
        'edge_weight_mode': 'probabilistic_mroaw',
        'use_agentic_ppr_reset': True,
        'mroaw_use_info_likelihood': True,
        'mroaw_use_struct_likelihood': True,
        'mroaw_use_semantic_likelihood': True,
        'description': '完整方案（概率化MROAW + Agentic）⭐',
        'short_name': 'Full (Ours)'
    }
}

# 数据集列表
DATASETS = ['musique', '2wikimultihopqa', 'hotpotqa']

# 通用配置（所有实验共享）
COMMON_CONFIG = {
    'llm_name': 'meta-llama/Llama-3.3-70B-Instruct',
    'embedding_name': 'nvidia/NV-Embed-v2',
    'llm_base_url': 'https://openrouter.ai/api/v1',  # 或您的vLLM服务器地址
}

# 关键评估指标
KEY_METRICS = {
    'primary': ['recall@10', 'recall@20'],
    'secondary': ['recall@1', 'recall@5', 'recall@30', 'recall@50'],
    'qa': ['em', 'f1'],
    'time': ['time_retrieval', 'time_ppr']
}

def get_config_command(config_name, dataset):
    """
    生成给定配置和数据集的完整命令行参数

    Args:
        config_name: 配置名称（ABLATION_CONFIGS的key）
        dataset: 数据集名称

    Returns:
        命令行参数字符串
    """
    config = ABLATION_CONFIGS[config_name]

    cmd_parts = [
        f"--dataset {dataset}",
        f"--llm_name {COMMON_CONFIG['llm_name']}",
        f"--embedding_name {COMMON_CONFIG['embedding_name']}",
        f"--llm_base_url {COMMON_CONFIG['llm_base_url']}",
        f"--edge_weight_mode {config['edge_weight_mode']}",
    ]

    # Agentic PPR配置
    if config.get('use_agentic_ppr_reset', False):
        cmd_parts.append("--use_agentic_ppr_reset true")
    else:
        cmd_parts.append("--use_agentic_ppr_reset false")

    # 概率化MROAW的三个似然函数
    if config.get('mroaw_use_info_likelihood', False):
        cmd_parts.append("--mroaw_use_info_likelihood true")
    if config.get('mroaw_use_struct_likelihood', False):
        cmd_parts.append("--mroaw_use_struct_likelihood true")
    if config.get('mroaw_use_semantic_likelihood', False):
        cmd_parts.append("--mroaw_use_semantic_likelihood true")

    # 日志配置
    cmd_parts.extend([
        "--log_dir experiments/ablation_mroaw_agentic/logs",
        "--log_level INFO"
    ])

    return " ".join(cmd_parts)

def print_experiment_matrix():
    """打印实验矩阵（6配置 × 3数据集 = 18组）"""
    print("\n" + "="*80)
    print("实验矩阵：6配置 × 3数据集 = 18组实验")
    print("="*80)

    for i, config_name in enumerate(ABLATION_CONFIGS.keys(), 1):
        config = ABLATION_CONFIGS[config_name]
        print(f"\n[配置 {i}/6] {config['short_name']}")
        print(f"  描述: {config['description']}")
        print(f"  数据集: {', '.join(DATASETS)}")

    print("\n" + "="*80)
    print(f"总计: {len(ABLATION_CONFIGS)} × {len(DATASETS)} = {len(ABLATION_CONFIGS) * len(DATASETS)} 组实验")
    print("="*80 + "\n")

if __name__ == '__main__':
    # 测试：打印实验矩阵
    print_experiment_matrix()

    # 测试：生成示例命令
    print("\n示例命令（baseline + musique）:")
    print("-" * 80)
    cmd = get_config_command('baseline', 'musique')
    print(f"python main.py {cmd}")
    print("-" * 80)
