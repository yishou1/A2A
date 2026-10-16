"""Run real Newport retrievals and archive their requests, results and traces."""

import argparse
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--output-dir', type=Path, default=Path('outputs/real_trace_demo'))
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    workflow = 'wf-synapserag-real-' + stamp
    folder = args.output_dir / workflow
    folder.mkdir(parents=True, exist_ok=True)

    def save(name, value):
        (folder / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

    def request(path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(args.base_url + path, data=data,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=240) as response:
            return json.load(response)

    cases = {
        'planning': [
            ('ROE-PLANNING-AUTHORITY', 'Identify the ROE request and approval procedures a commander should check while preparing a monitoring and de-escalation plan. Include the role of higher authority.'),
            ('ROE-PLANNING-CONSTRAINTS', 'Find the handbook provisions on necessity, proportionate response and advance warnings that constrain a proposed response to a potential threat.'),
        ],
        'compliance': [
            ('ROE-COMPLIANCE-WARNINGS', 'Retrieve the original requirements for warnings and de-escalation, and the necessity and proportionality checks required before force is used.'),
            ('ROE-COMPLIANCE-AUTHORIZATION', 'Locate the ROEREQ request and ROEAUTH authorization examples, including the distinction between a requested rule and a rule approved by the authorized commander.'),
        ],
    }
    summary = {'workflow_id': workflow, 'execution_mode': 'live_retrieval', 'traces': []}
    print('workflow_id=' + workflow, flush=True)
    for purpose, queries in cases.items():
        role = 'decision_planning_agent' if purpose == 'planning' else 'compliance_authorization_agent'
        task_id = workflow + ':' + role
        payload = {
            'schema_version': '1.0', 'request_id': purpose + '-' + stamp,
            'purpose': purpose, 'top_k': 3,
            'context': {'workflow_id': workflow, 'task_id': task_id,
                        'work_item_id': task_id, 'agent_id': role},
            'explain': {'enabled': True, 'level': 'detailed'},
            'queries': [{'query_id': key, 'text': text} for key, text in queries],
        }
        save(purpose + '_request.json', payload)
        print('Running ' + purpose, flush=True)
        result = request('/api/retrieve', payload)
        save(purpose + '_response.json', result)
        trace = request('/api/retrieval-traces/' + result['trace_id'])
        save(purpose + '_trace.json', trace)
        row = {'purpose': purpose, 'trace_id': trace['trace_id'],
               'duration_ms': trace['duration_ms'], 'warnings': trace['warnings'], 'queries': []}
        for query in trace['queries']:
            row['queries'].append({
                'query_id': query['query_id'], 'timings': query['stage_timings_ms'],
                'fact_rerank': query.get('fact_rerank'),
                'fallback_events': query.get('fallback_events'),
                'evidence_count': len(query['evidence']),
                'nodes': len(query['graph_overlay']['nodes']),
                'edges': len(query['graph_overlay']['edges']),
                'path_hops': [item['hop_count'] for item in query['evidence_paths']],
            })
        summary['traces'].append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    summary['demo_url'] = 'http://127.0.0.1:5055/?retrieval_workflow=' + workflow
    save('summary.json', summary)
    print(summary['demo_url'], flush=True)
    print('Artifacts: ' + str(folder), flush=True)


if __name__ == '__main__':
    main()
