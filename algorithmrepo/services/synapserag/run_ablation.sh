#!/bin/bash
# SynapseRAG 消融实验运行脚本

python main_ablation.py \
    --dataset 2wikimultihopqa \
    --llm_name meta-llama/llama-3.3-70b-instruct \
    --embedding_name nvidia/NV-Embed-v2 \
    --llm_base_url https://openrouter.ai/api/v1 \
    --edge_weight_mode uniform \
    --use_agentic_ppr_reset false \
    --log_dir experiments/ablation_mroaw_agentic/logs \
    --log_level INFO
