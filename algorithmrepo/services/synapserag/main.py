import os
from typing import List
import json
from datetime import datetime

from src.synapserag.SynapseRAG import SynapseRAG
from src.synapserag.utils.misc_utils import string_to_bool
from src.synapserag.utils.config_utils import BaseConfig, LLMEndpointConfig
from src.synapserag.utils.logging_utils import setup_logging

import argparse

# os.environ["LOG_LEVEL"] = "DEBUG"
os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

import logging

def get_gold_docs(samples: List, dataset_name: str = None) -> List:
    gold_docs = []
    for sample in samples:
        if 'supporting_facts' in sample:  # hotpotqa, 2wikimultihopqa
            gold_title = set([item[0] for item in sample['supporting_facts']])
            gold_title_and_content_list = [item for item in sample['context'] if item[0] in gold_title]
            if dataset_name.startswith('hotpotqa'):
                gold_doc = [item[0] + '\n' + ''.join(item[1]) for item in gold_title_and_content_list]
            else:
                gold_doc = [item[0] + '\n' + ' '.join(item[1]) for item in gold_title_and_content_list]
        elif 'contexts' in sample:
            gold_doc = [item['title'] + '\n' + item['text'] for item in sample['contexts'] if item['is_supporting']]
        else:
            assert 'paragraphs' in sample, "`paragraphs` should be in sample, or consider the setting not to evaluate retrieval"
            gold_paragraphs = []
            for item in sample['paragraphs']:
                if 'is_supporting' in item and item['is_supporting'] is False:
                    continue
                gold_paragraphs.append(item)
            gold_doc = [item['title'] + '\n' + (item['text'] if 'text' in item else item['paragraph_text']) for item in gold_paragraphs]

        gold_doc = list(set(gold_doc))
        gold_docs.append(gold_doc)
    return gold_docs


def get_gold_answers(samples):
    gold_answers = []
    for sample_idx in range(len(samples)):
        gold_ans = None
        sample = samples[sample_idx]

        if 'answer' in sample or 'gold_ans' in sample:
            gold_ans = sample['answer'] if 'answer' in sample else sample['gold_ans']
        elif 'reference' in sample:
            gold_ans = sample['reference']
        elif 'obj' in sample:
            gold_ans = set(
                [sample['obj']] + [sample['possible_answers']] + [sample['o_wiki_title']] + [sample['o_aliases']])
            gold_ans = list(gold_ans)
        assert gold_ans is not None
        if isinstance(gold_ans, str):
            gold_ans = [gold_ans]
        assert isinstance(gold_ans, list)
        gold_ans = set(gold_ans)
        if 'answer_aliases' in sample:
            gold_ans.update(sample['answer_aliases'])

        gold_answers.append(gold_ans)

    return gold_answers

def main():
    parser = argparse.ArgumentParser(description="SynapseRAG retrieval and QA")
    parser.add_argument('--dataset', type=str, default='musique', help='Dataset name')
    parser.add_argument('--llm_base_url', type=str, default='https://openrouter.ai/api/v1', help='LLM base URL')
    parser.add_argument('--llm_name', type=str, default='meta-llama/llama-3.3-70b-instruct', help='LLM name')
    parser.add_argument('--qa_llm_base_url', type=str, default=None, help='QA/rerank LLM base URL')
    parser.add_argument('--qa_llm_name', type=str, default=None, help='QA/rerank LLM name')
    parser.add_argument('--openie_llm_base_url', type=str, default=None, help='Dedicated OpenIE LLM base URL')
    parser.add_argument('--openie_llm_name', type=str, default=None, help='Dedicated OpenIE LLM name')
    parser.add_argument('--embedding_name', type=str, default='nvidia/NV-Embed-v2', help='embedding model name')
    parser.add_argument('--embedding_base_url', type=str, default=None, help='Embedding API base URL')
    parser.add_argument('--index_id', type=str, default=None, help='Stable index ID independent of QA model')
    parser.add_argument('--stage', choices=['all', 'openie', 'build-index', 'query'], default='all')
    parser.add_argument('--fact_rerank_mode', choices=['embedding', 'llm', 'hybrid'], default='hybrid')
    parser.add_argument('--fact_candidate_top_k', type=int, default=20)
    parser.add_argument('--fact_rerank_top_k', type=int, default=5)
    parser.add_argument('--force_index_from_scratch', type=str, default='false',
                        help='If set to True, will ignore all existing storage files and graph data and will rebuild from scratch.')
    parser.add_argument('--force_openie_from_scratch', type=str, default='false', help='If set to False, will try to first reuse openie results for the corpus if they exist.')
    parser.add_argument('--force_rebuild_graph', type=str, default='false',
                        help='If set to True, will force rebuild graph topology from scratch, ignoring cached edge info.')
    parser.add_argument('--openie_mode', choices=['online', 'offline'], default='online',
                        help="OpenIE mode, offline denotes using VLLM offline batch mode for indexing, while online denotes")
    parser.add_argument('--save_dir', type=str, default='outputs', help='Save directory')
    parser.add_argument('--log_dir', type=str, default=None, help='Directory to persist log files (defaults to <save_dir>/logs)')
    parser.add_argument('--log_level', type=str, default=os.getenv("LOG_LEVEL", "INFO"), help='Logging verbosity (e.g., DEBUG, INFO)')
    args = parser.parse_args()

    dataset_name = args.dataset
    save_dir = args.save_dir
    llm_base_url = args.llm_base_url
    llm_name = args.llm_name
    qa_llm_name = args.qa_llm_name or llm_name
    qa_llm_base_url = args.qa_llm_base_url or llm_base_url
    if save_dir == 'outputs':
        save_dir = save_dir + '/' + dataset_name
    else:
        save_dir = save_dir + '_' + dataset_name

    corpus_path = f"reproduce/dataset/{dataset_name}_corpus.json"
    with open(corpus_path, "r") as f:
        corpus = json.load(f)

    docs = [f"{doc['title']}\n{doc['text']}" for doc in corpus]

    force_index_from_scratch = string_to_bool(args.force_index_from_scratch)
    force_openie_from_scratch = string_to_bool(args.force_openie_from_scratch)
    force_rebuild_graph = string_to_bool(args.force_rebuild_graph)

    # Prepare datasets and evaluation
    samples = json.load(open(f"reproduce/dataset/{dataset_name}.json", "r"))
    all_queries = [s['question'] for s in samples]

    gold_answers = get_gold_answers(samples)
    try:
        gold_docs = get_gold_docs(samples, dataset_name)
        assert len(all_queries) == len(gold_docs) == len(gold_answers), "Length of queries, gold_docs, and gold_answers should be the same."
    except:
        gold_docs = None

    qa_extra_body = {"reasoning_effort": "none"} if "qwen3" in qa_llm_name.lower() else {}
    qa_endpoint = LLMEndpointConfig(
        model_name=qa_llm_name,
        base_url=qa_llm_base_url,
        api_key_env="SYNAPSERAG_QA_API_KEY",
        temperature=0.1 if "qwen3" in qa_llm_name.lower() else 0.0,
        max_tokens=1024,
        extra_body=qa_extra_body,
    )
    openie_endpoint = None
    if args.openie_llm_name or args.openie_llm_base_url:
        openie_endpoint = LLMEndpointConfig(
            model_name=args.openie_llm_name or llm_name,
            base_url=args.openie_llm_base_url or llm_base_url,
            api_key_env="SYNAPSERAG_OPENIE_API_KEY",
            temperature=0.0,
            max_tokens=2048,
        )

    config = BaseConfig(
        save_dir=save_dir,
        llm_base_url=llm_base_url,
        llm_name=llm_name,
        qa_llm=qa_endpoint,
        openie_llm=openie_endpoint,
        dataset=dataset_name,
        embedding_model_name=args.embedding_name,
        embedding_base_url=args.embedding_base_url,
        index_id=args.index_id,
        runtime_stage=args.stage,
        fact_rerank_mode=args.fact_rerank_mode,
        fact_candidate_top_k=args.fact_candidate_top_k,
        fact_rerank_top_k=args.fact_rerank_top_k,
        force_index_from_scratch=force_index_from_scratch,  # ignore previously stored index, set it to False if you want to use the previously stored index and embeddings
        force_openie_from_scratch=force_openie_from_scratch,
        force_rebuild_graph=force_rebuild_graph,
        rerank_dspy_file_path="src/synapserag/prompts/dspy_prompts/filter_llama3.3-70B-Instruct.json",
        retrieval_top_k=200,
        linking_top_k=5,
        max_qa_steps=3,
        qa_top_k=5,
        graph_type="facts_and_sim_passage_node_unidirectional",
        embedding_batch_size=8,
        max_new_tokens=None,
        corpus_len=len(corpus),
        openie_mode=args.openie_mode
    )

    run_name = f"{dataset_name}-{args.llm_name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    log_dir = args.log_dir or os.path.join(save_dir, "logs")
    log_path = setup_logging(log_dir=log_dir, run_name=run_name, level=args.log_level)
    logger = logging.getLogger(__name__)
    logger.info("Writing logs to %s", log_path)

    synapserag = SynapseRAG(global_config=config)

    if args.stage == 'openie':
        synapserag.extract_openie(docs, force=force_openie_from_scratch)
        return
    if args.stage == 'build-index':
        synapserag.build_index(docs, allow_openie_calls=False)
        return
    if args.stage == 'query':
        synapserag.load_index()
    else:
        synapserag.index(docs)

    # Retrieval and QA
    if gold_docs is not None:
        # 先执行检索
        retrieval_results, overall_retrieval_result = synapserag.retrieve(
            queries=all_queries, gold_docs=gold_docs)

        # 然后执行QA
        retrieval_results, qa_responses, metadata, overall_qa_result = synapserag.rag_qa(
            queries=retrieval_results, gold_docs=gold_docs, gold_answers=gold_answers)

        # 保存实验结果
        experiment_name = f"{dataset_name}_{args.llm_name.split('/')[-1]}"
        synapserag.save_experiment_results(
            experiment_name=experiment_name,
            queries=all_queries,
            retrieval_results=retrieval_results,
            qa_responses=qa_responses,
            overall_retrieval_result=overall_retrieval_result,
            overall_qa_result=overall_qa_result,
            gold_docs=gold_docs,
            gold_answers=gold_answers
        )
    else:
        synapserag.rag_qa(queries=all_queries, gold_docs=gold_docs, gold_answers=gold_answers)

if __name__ == "__main__":
    main()
