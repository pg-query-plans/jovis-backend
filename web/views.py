import psycopg2
import os
import time
import re
import os
import time
import re

from django.shortcuts import render

from rest_framework.views import APIView
from rest_framework.response import Response

from backend.settings import PG_LOG_FILE, PG_LOG_BACKUP_DIR

def clear_previous_log():
    os.system(f"cp {PG_LOG_FILE} {PG_LOG_BACKUP_DIR}/{time.time()}_prev")
    os.system(f"echo '' > {PG_LOG_FILE}")

def read_and_clear_log():
    filename = f"{PG_LOG_BACKUP_DIR}/{time.time()}_pq"

    # TODO: I have no idea why logfile from pg_ctl does not suppress 
    #    STATEMENT even though I turned off log configurations.
    # So, I manually remove the statements here to save disk and parsing cost.
    f1 = open(PG_LOG_FILE, 'r')
    ret = []
    recent_removed = False
    for line in f1.readlines():
        if 'STATEMENT' in line:
            recent_removed = True
            continue

        if recent_removed and line[0] == '\t':
            continue

        recent_removed = False
        ret.append(line)

    f1.close()

    os.system(f"echo '' > {PG_LOG_FILE}")

    f2 = open(filename, 'w')
    for line in ret:
        f2.write(line)
    f2.close()

    return ret


def parse_path_with_state_machine(logs: list, cur: int):
    """
    state list:
    PathHeader, PathSeqScan, PathIdxScan, PathKeys, PathJoin, PathMJoin, PathOuter, PathInner
    PathWait, PathWait2
    PathDone
    """

    state = 'PathHeader'
    path_buffer = {}

    while state != 'PathDone' and cur < len(logs):
        line = logs[cur].strip()
        print(cur, state, line)
        #input()

        if state == 'PathHeader':
            _PATHHEADER_EXP = r'\ *(\w*)\((.*)\) required_outer \((\w*)\) rows=(\d*) cost=(\d*\.\d*)\.\.(\d*\.\d*)'
            _PATHHEADER_EXP_NOPARAM = r'\ *(\w*)\((.*)\) rows=(\d*) cost=(\d*\.\d*)\.\.(\d*\.\d*)'           
            # get the header that is must be in the logs
            header = re.match(_PATHHEADER_EXP, line)
            node, relid, ro_relid, rows, startup_cost, total_cost = None, None, None, None, None, None
            if header:
                node, relid, ro_relid, rows, startup_cost, total_cost = header.groups()
            elif header := re.match(_PATHHEADER_EXP_NOPARAM, line):
                node, relid, rows, startup_cost, total_cost = header.groups()
            if node: 
                path_buffer['node'] = node
            if relid:
                path_buffer['relid'] = relid
            if ro_relid:
                path_buffer['ro_relid'] = ro_relid
            if rows:
                path_buffer['rows'] = int(rows)
            if startup_cost:
                path_buffer['startup_cost'] = float(startup_cost)
            if total_cost:
                path_buffer['total_cost'] = float(total_cost)

            if node in ['SeqScan', 'IdxScan', 'GatherMerge', 'BitmapHeapScan', 'HashJoin', 'MergeJoin', 'NestLoop']:
                state = 'PathDetail'
            else:
                state = 'PathWait'
            cur += 1

        elif state == 'PathDetail':
            if path_buffer['node'] == 'SeqScan':
                _SEQSCAN_DETAILS_EXP = r'\ *details: cpu_run_cost=(\d+\.\d+) disk_run_cost=(\d+\.\d+) tuples=(\d+) qual_cost=(\d+\.\d+) cpu_per_tuple=(\d+\.\d+) pages=(\d+\.\d+) spc_seq_page_cost=(\d+\.\d+) target_per_tuple=(\d+\.\d+)'
                details = re.match(_SEQSCAN_DETAILS_EXP, line)
                if details:
                    cpu_run_cost, disk_run_cost, tuples, qual_cost, cpu_per_tuple, pages, spc_seq_page_cost, target_per_tuple = details.groups()
                    path_buffer.update({
                        'cpu_run_cost': float(cpu_run_cost),
                        'disk_run_cost': float(disk_run_cost),
                        'tuples': int(tuples),
                        'qual_cost': float(qual_cost),
                        'cpu_per_tuple': float(cpu_per_tuple),
                        'pages': float(pages),
                        'spc_seq_page_cost': float(spc_seq_page_cost),
                        'target_per_tuple': float(target_per_tuple)
                    })
            elif path_buffer['node'] == 'BitmapHeapScan':
                _BITMAPHEAPSCAN_DETAILS_EXP = r'\ *details: cpu_run_cost=(\d+\.\d+) tuples=(\d+) cpu_per_tuple=(\d+\.\d+) target_per_tuple=(\d+\.\d+) qual_cost=(\d+\.\d+) pages=(\d+\.\d+) spc_seq_page_cost=(\d+\.\d+) spc_random_page_cost=(\d+\.\d+) T=(\d+\.\d+) index_total_cost=(\d+\.\d+) cost_per_page=(\d+\.\d+)'
                details = re.match(_BITMAPHEAPSCAN_DETAILS_EXP, line)
                if details:
                    cpu_run_cost, tuples, cpu_per_tuple, target_per_tuple, qual_cost, pages, spc_seq_page_cost, spc_random_page_cost, T, index_total_cost, cost_per_page = details.groups()
                    path_buffer.update({
                        'cpu_run_cost': float(cpu_run_cost),
                        'tuples': int(tuples),
                        'cpu_per_tuple': float(cpu_per_tuple),
                        'target_per_tuple': float(target_per_tuple),
                        'qual_cost': float(qual_cost),
                        'pages': float(pages),
                        'spc_seq_page_cost': float(spc_seq_page_cost),
                        'spc_random_page_cost': float(spc_random_page_cost),
                        'T': float(T),
                        'index_total_cost': float(index_total_cost),
                        'cost_per_page': float(cost_per_page)
                    })
            elif path_buffer['node'] == 'IdxScan':
                _IDXSCAN_DETAILS_EXP = r'\ *details: selectivity=(\d+\.\d+) tuples_fetched=(\d+) index_startup_cost=(\d+\.\d+) index_total_cost=(\d+\.\d+) cpu_per_tuple=(\d+\.\d+) min_IO_cost=(\d+\.\d+) max_IO_cost=(\d+\.\d+) index_correlation=(\d+\.\d+) pages_fetched=(\d+) c\^2=(\d+\.\d+)'
                details = re.match(_IDXSCAN_DETAILS_EXP, line)
                if details:
                    selectivity, tuples_fetched, index_startup_cost, index_total_cost, cpu_per_tuple, min_IO_cost, max_IO_cost, index_correlation, pages_fetched, csquared = details.groups()
                    path_buffer.update({
                        'selectivity': float(selectivity),
                        'tuples_fetched': int(tuples_fetched),
                        'index_startup_cost': float(index_startup_cost),
                        'index_total_cost': float(index_total_cost),
                        'cpu_per_tuple': float(cpu_per_tuple),
                        'min_IO_cost': float(min_IO_cost),
                        'max_IO_cost': float(max_IO_cost),
                        'index_correlation': float(index_correlation),
                        'pages_fetched': int(pages_fetched),
                        'csquared': float(csquared)
                    })
            elif path_buffer['node'] == 'GatherMerge':
                _GATHERMERGE_DETAILS_EXP = r'\ *details: comparison_cost=(\d+\.\d+) cpu_operator_cost=(\d+\.\d+) N=(\d+) input_startup_cost=(\d+\.\d+)'
                details = re.match(_GATHERMERGE_DETAILS_EXP, line)
                if details:
                    comparison_cost, cpu_operator_cost, N, input_startup_cost = details.groups()
                    path_buffer.update({
                        'comparison_cost': float(comparison_cost),
                        'cpu_operator_cost': float(cpu_operator_cost),
                        'N': int(N),
                        'input_startup_cost': float(input_startup_cost)
                    })
            elif path_buffer['node'] == 'NestLoop':
                _NESTLOOP_DETAILS_EXP = r'\ *details: initial_startup_cost=(\d+\.\d+) initial_run_cost=(\d+\.\d+) inner_run_cost=(\d+\.\d+) inner_rescan_run_cost=(\d+\.\d+) inner_rescan_start_cost=(\d+\.\d+) inner_path_startup=(\d+\.\d+) outer_rows=(\d+\.\d+) outer_path_startup=(\d+\.\d+) outer_path_run=(\d+\.\d+) ntuples=(\d+\.\d+) cpu_per_tuple=(\d+\.\d+) matched_outer_tuple_cost=(\d+\.\d+) unmatched_outer_tuple_cost=(\d+\.\d+) inner_scan_cost=(\d+\.\d+)'
                details = re.match(_NESTLOOP_DETAILS_EXP, line)
                if details:
                    initial_startup_cost, initial_run_cost, inner_run_cost, inner_rescan_run_cost, inner_rescan_start_cost, inner_path_startup, outer_rows, outer_path_startup, outer_path_run, ntuples, cpu_per_tuple, matched_outer_tuple_cost, unmatched_outer_tuple_cost, inner_scan_cost = details.groups()
                    path_buffer.update({
                        'initial_startup_cost': float(initial_startup_cost),
                        'initial_run_cost': float(initial_run_cost),
                        'inner_run_cost': float(inner_run_cost),
                        'inner_rescan_run_cost': float(inner_rescan_run_cost),
                        'inner_rescan_start_cost': float(inner_rescan_start_cost),
                        'inner_path_startup': float(inner_path_startup),
                        'outer_rows': float(outer_rows),
                        'outer_path_startup': float(outer_path_startup),
                        'outer_path_run': float(outer_path_run),
                        'ntuples': float(ntuples),
                        'cpu_per_tuple': float(cpu_per_tuple),
                        'matched_outer_tuple_cost': float(matched_outer_tuple_cost),
                        'unmatched_outer_tuple_cost': float(unmatched_outer_tuple_cost),
                        'inner_scan_cost': float(inner_scan_cost)
                    })
            elif path_buffer['node'] == 'MergeJoin':
                _MERGEJOIN_DETAILS_EXP = r'\ *details: sortouter=(\d+) sortinner=(\d+) materializeinner=(\d+) initial_run_cost=(\d+\.\d+) initial_startup_cost=(\d+\.\d+) inner_scan_cost=(\d+\.\d+) inner_startup_cost=(\d+\.\d+) outer_run_cost=(\d+\.\d+) outer_scan_cost=(\d+\.\d+) outer_startup_cost=(\d+\.\d+) mergejointuples=(\d+\.\d+) bare_inner_cost=(\d+\.\d+) mat_inner_cost=(\d+\.\d+) merge_eval_cost=(\d+\.\d+) merge_init_eval_cost=(\d+\.\d+)'
                details = re.match(_MERGEJOIN_DETAILS_EXP, line)
                if details:
                    sortouter, sortinner, materializeinner, initial_run_cost, initial_startup_cost, inner_scan_cost, inner_startup_cost, outer_run_cost, outer_scan_cost, outer_startup_cost, mergejointuples, bare_inner_cost, mat_inner_cost, merge_eval_cost, merge_init_eval_cost = details.groups()
                    path_buffer.update({
                        'sortouter': int(sortouter),
                        'sortinner': int(sortinner),
                        'materializeinner': int(materializeinner),
                        'initial_run_cost': float(initial_run_cost),
                        'initial_startup_cost': float(initial_startup_cost),
                        'inner_scan_cost': float(inner_scan_cost),
                        'inner_startup_cost': float(inner_startup_cost),
                        'outer_run_cost': float(outer_run_cost),
                        'outer_scan_cost': float(outer_scan_cost),
                        'outer_startup_cost': float(outer_startup_cost),
                        'mergejointuples': float(mergejointuples),
                        'bare_inner_cost': float(bare_inner_cost),
                        'mat_inner_cost': float(mat_inner_cost),
                        'merge_eval_cost': float(merge_eval_cost),
                        'merge_init_eval_cost': float(merge_init_eval_cost)
                    })
            elif path_buffer['node'] == 'HashJoin':
                _HASHJOIN_DETAILS_EXP = r'\s*details:\s*initial_startup_cost=(\d+\.\d+)\s*initial_run_cost=(\d+\.\d+)\s*outer_path_startup=(\d+\.\d+)\s*outer_path_total=(\d+\.\d+)\s*inner_path_startup=(\d+\.\d+)\s*inner_path_total=(\d+\.\d+)\s*cpu_operator_cost=(\d+\.\d+)\s*num_hashclauses=(\d+)\s*cpu_tuple_cost=(\d+\.\d+)\s*inner_path_rows=(\d+\.\d+)\s*hashcpu_cost=(\d+\.\d+)\s*outer_path_rows=(\d+\.\d+)\s*innerpages=(\d+\.\d+)\s*outerpages=(\d+\.\d+)\s*seqpage_cost=(\d+\.\d+)\s*hashjointuples=(\d+\.\d+)\s*cpu_per_tuple=(\d+\.\d+)\s*hash_qual_eval_cost=(\d+\.\d+)'
                details = re.match(_HASHJOIN_DETAILS_EXP, line)
                if details:
                    initial_startup_cost, initial_run_cost, outer_path_startup, outer_path_total, inner_path_startup, inner_path_total, cpu_operator_cost, num_hashclauses, cpu_tuple_cost, inner_path_rows, hashcpu_cost, outer_path_rows, innerpages, outerpages, seqpage_cost, hashjointuples, cpu_per_tuple, hash_qual_eval_cost = details.groups()
                    path_buffer.update({
                        'initial_startup_cost': float(initial_startup_cost),
                        'initial_run_cost': float(initial_run_cost),
                        'outer_path_startup': float(outer_path_startup),
                        'outer_path_total': float(outer_path_total),
                        'inner_path_startup': float(inner_path_startup),
                        'inner_path_total': float(inner_path_total),
                        'cpu_operator_cost': float(cpu_operator_cost),
                        'num_hashclauses': int(num_hashclauses),
                        'cpu_tuple_cost': float(cpu_tuple_cost),
                        'inner_path_rows': float(inner_path_rows),
                        'hashcpu_cost': float(hashcpu_cost),
                        'outer_path_rows': float(outer_path_rows),
                        'innerpages': float(innerpages),
                        'outerpages': float(outerpages),
                        'seqpage_cost': float(seqpage_cost),
                        'hashjointuples': float(hashjointuples),
                        'cpu_per_tuple': float(cpu_per_tuple),
                        'hash_qual_eval_cost': float(hash_qual_eval_cost)
                    })

            state = 'PathWait'
            cur += 1
        
        elif state == 'PathWait':
            # a temp state to decide if it is PathKeys, PathJoin, or PathMJoin
            _PATHKEYS_EXP = r'\ *pathkeys:\ (.*)'
            _CLAUSES_EXP = r'\ *clauses:(.*)'
            #_MERGEJOIN_INFO_EXP = r'\ *sortouter=(\d) sortinner=(\d) materializeinner=(\d)'

            if re.match(_PATHKEYS_EXP, line):
                state = 'PathKeys'
            elif re.match(_CLAUSES_EXP, line):
                state = 'PathJoin'
            #elif re.match(_MERGEJOIN_INFO_EXP, line):
            #    state = 'PathMJoin'
            else:
                # check indentation width
                raw_cur_line, raw_prev_line = logs[cur].replace('\t', '    '), logs[cur-1].replace('\t', '    ')
                cur_indent = len(raw_cur_line) - len(raw_cur_line.lstrip())
                prev_indent = len(raw_prev_line) - len(raw_prev_line.lstrip())
                is_sub = prev_indent < cur_indent
                if is_sub:
                    state = 'PathSub'
                else:
                    state = 'PathDone'

        elif state == 'PathKeys':
            _PATHKEYS_EXP = r'\ *pathkeys:\ (.*)'
            pathkeys = re.match(_PATHKEYS_EXP, line)
            assert(pathkeys)
            path_buffer['pathkeys'] = pathkeys.groups()[0].strip()

            state = 'PathWait'
            cur += 1

        elif state == 'PathJoin':
            _CLAUSES_EXP = r'\ *clauses:(.*)'
            clauses = re.match(_CLAUSES_EXP, line)
            assert(clauses)

            path_buffer['join'] = {
                'clauses': clauses.groups()[0].strip()
            }

            state = 'PathOuter'
            cur += 1

        elif state == 'PathOuter':
            outer, _cur = parse_path_with_state_machine(logs, cur)
            path_buffer['join']['outer'] = outer

            state = 'PathInner'
            cur = _cur

        elif state == 'PathInner':
            inner, _cur = parse_path_with_state_machine(logs, cur)
            path_buffer['join']['inner'] = inner 

            state = 'PathWait3'
            cur = _cur

        elif state == 'PathSub':
            sub, _cur = parse_path_with_state_machine(logs, cur)
            path_buffer['sub'] = sub

            state = 'PathDone'
            cur = _cur

        elif state == 'PathWait3':
            raw_cur_line, raw_prev_line = logs[cur].replace('\t', '    '), logs[cur-1].replace('\t', '    ')
            cur_indent = len(raw_cur_line) - len(raw_cur_line.lstrip())
            prev_indent = len(raw_prev_line) - len(raw_prev_line.lstrip())
            is_super = prev_indent > cur_indent
            if is_super:
                state = 'PathDone'
            else:
                state = 'PathSub'

    return path_buffer, cur

    

def parse_with_state_machine(logs: list, cur: int, _START_SIGN: str, _END_SIGN: str):
    """
    state list:
        Start
        RelOptHeader, RelOptPathlist
        Path (PathHeader, PathKeys, PathJoin, PathMJoin)
        Done
    """
    state = 'Start'
    buffer = {}

    while state != 'Done' and cur < len(logs):
        line = logs[cur].strip()
        print(cur, state, line)

        if state == 'Start':
            if _START_SIGN in line:
                state = 'RelOptHeader'

            cur += 1

        elif state == 'RelOptHeader':
            _RELINFO_EXP = r'RELOPTINFO \((.*)\): rows=(\d*) width=(\d*)'

            # get relinfo that is must be in the logs
            relinfo = re.match(_RELINFO_EXP, line)
            assert(relinfo)

            relid, rows, width = relinfo.groups()
            buffer = {
                'relid': relid,
                'rows': int(rows),
                'width': int(width)
            }

            state = 'Wait'
            cur += 1

        elif state == 'Wait':
            _PATH_LIST_EXP = 'path list:'
            _CHEAPESTPARAMPATH_LIST_EXP = 'cheapest parameterized paths:'
            _CHEAPESTSTARTUPPATH_EXP = 'cheapest startup path:'
            _CHEAPESTTOTALPATH_EXP = 'cheapest total path:'

            if _PATH_LIST_EXP in line:
                state = 'PathList'
            elif _CHEAPESTPARAMPATH_LIST_EXP in line:
                state = 'CheapestParamPathList'
            elif _CHEAPESTSTARTUPPATH_EXP in line:
                state = 'CheapestStartupPath'
                cur += 1
            elif _CHEAPESTTOTALPATH_EXP in line:
                state = 'CheapestTotalPath'
                cur += 1
            elif _END_SIGN in line:
                state = 'Done'
                cur += 1
            else:
                cur += 1

        elif state == 'PathList':
            buffer['paths'] = []

            state = 'Path'
            cur += 1

        elif state == 'Path':
            _path_buffer, _cur = parse_path_with_state_machine(logs, cur)
            buffer['paths'].append(_path_buffer)

            state = 'PathContinue'
            cur = _cur

        elif state == 'PathContinue':
            strip = line.replace('\t', '').replace('\n', '').strip()
            if strip != '':
                state = 'Path'
            else:
                state = 'Wait'
                cur += 1

        elif state == 'CheapestParamPathList':
            buffer['cheapest_param_paths'] = []

            state = 'CheapestParamPath'
            cur += 1

        elif state == 'CheapestParamPath':
            _path_buffer, _cur = parse_path_with_state_machine(logs, cur)
            buffer['cheapest_param_paths'].append(_path_buffer)

            state = 'CheapestParamPathContinue'
            cur = _cur

        elif state == 'CheapestParamPathContinue':
            strip = line.replace('\t', '').replace('\n', '').strip()
            if strip != '':
                state = 'CheapestParamPath'
            else:
                state = 'Wait'
                cur += 1

        elif state == 'CheapestStartupPath':
            _path_buffer, _cur = parse_path_with_state_machine(logs, cur)
            buffer['cheapest_startup_paths'] = _path_buffer

            state = 'Wait'
            cur = _cur

        elif state == 'CheapestTotalPath':
            _path_buffer, _cur = parse_path_with_state_machine(logs, cur)
            buffer['cheapest_total_paths'] = _path_buffer

            state = 'Wait'
            cur = _cur

    return buffer, cur


def get_base_path(log_lines: list, cur: int):
    _START_SIGN = '[VPQO][BASE] set_rel_pathlist started'
    _END_SIGN = '[VPQO][BASE] set_rel_pathlist done'
    return parse_with_state_machine(log_lines, cur, _START_SIGN, _END_SIGN)


def get_dp_path(log_lines: list, cur: int):
    _START_SIGN = '[VPQO][DP] standard_join_search started'
    _END_SIGN = '[VPQO][DP] standard_join_search done'
    return parse_with_state_machine(log_lines, cur, _START_SIGN, _END_SIGN)

def parse_geqo_with_state_machine(logs: list):
    """
    scan all logs and parse geqo data
    """
    cur = 0
    state = 'Init'
    buffer = {}
    tmpbuffer = {}

    while cur < len(logs):
        line = logs[cur].strip()
        print(cur, state, line)

        if state == 'Init':
            _INIT_EXP = r'.*\[VPQO\]\[GEQO\] GEQO selected (\d*) pool entries, best (\d*\.\d*), worst (\d*\.\d*)'
            initinfo = re.match(_INIT_EXP, line)
            if initinfo is None:
                cur += 1
                continue

            pool_size, best, worst = initinfo.groups()
            buffer['pool_size'] = int(pool_size)
            buffer['init'] = {'best': float(best), 'worst': float(worst)}
            buffer['gen'] = []

            state = 'Mapping'
            cur += 1
        
        elif state == 'Mapping':
            _MAPPING_EXP = r'\[VPQO\]\[GEQO\] gene=(\d*) => relids=(.*)'
            mapinfo = re.match(_MAPPING_EXP, line)
            if mapinfo is None:
                if 'map' not in buffer:
                    # skip until reaching mapping lines
                    cur += 1
                    continue
                else:
                    # end of the state
                    state = 'Wait'
                    continue

            if 'map' not in buffer:
                buffer['map'] = {}

            gene, relids = mapinfo.groups()
            buffer['map'][gene] = relids
            cur += 1

        elif state == 'Wait':
            _GENERATION_EXP = r'.*\[GEQO\] *(\-?\d*).*Best: (.*)  Worst: (.*)  Mean: (.*)  Avg: (.*)'
            _OFFSPRING1_EXP = r'\[VPQO\]\[GEQO\] parents=\[(\d*), (\d*)\]'
            if re.match(_GENERATION_EXP, line):
                state = 'Gen'
            elif re.match(_OFFSPRING1_EXP, line):
                state = 'Offspring'
            else:
                cur += 1


        elif state == 'Offspring':
            _OFFSPRING1_EXP = r'\[VPQO\]\[GEQO\] parents=\[(\d*), (\d*)\]'

            offspringinfo = re.match(_OFFSPRING1_EXP, line)
            if offspringinfo:
                parent1, parent2 = offspringinfo.groups()
                tmpbuffer = {
                    'parents': [int(parent1), int(parent2)]
                }
                cur += 1
            else:
                # FIXME: This should be saperated into multiple states
                _GENERATION_EXP = r'.*\[GEQO\] *(\-?\d*).*Best: (.*)  Worst: (.*)  Mean: (.*)  Avg: (.*)'
                geninfo = re.match(_GENERATION_EXP, line)
                if geninfo:
                    # We should jump to the state 'Gen' cuz there is no newone_idx
                    state = 'Gen'
                    continue

                # Wait until we find newone_idx
                _OFFSPRING2_EXP = r'\[VPQO\]\[GEQO\] newone_idx=(\d*)'
                offspring2info = re.match(_OFFSPRING2_EXP, line)
                if offspring2info is None:
                    cur += 1
                    continue

                newone_idx = offspring2info.groups()[0]
                tmpbuffer['newone_idx'] = int(newone_idx)
                cur += 1
                state = 'Gen'

        elif state == 'Gen':
            _GENERATION_EXP = r'.*\[GEQO\] *(\-?\d*).*Best: (.*)  Worst: (.*)  Mean: (.*)  Avg: (.*)'
            geninfo = re.match(_GENERATION_EXP, line)
            if geninfo is None:
                cur += 1
                continue

            gen_num, best, worst, mean, avg = geninfo.groups()
            buffer['gen'].append({
                'gen_num': int(gen_num),
                'best': float(best),
                'worst': float(worst),
                'mean': float(mean),
                'avg': float(avg),
                'pool': []
            })

            state = 'Pool'
            cur += 1

        elif state == 'Pool':
            _POOL_EXP = r'\[GEQO\] (\d*)\)(.*) (.*)'
            poolinfo = re.match(_POOL_EXP, line)
            if poolinfo is None:
                state = 'Wait'
                cur += 1
                continue

            population_num, gene, fitness = poolinfo.groups()

            cur_idx = len(buffer['gen'][-1]['pool'])
            data = {
                'population_num': int(population_num),
                'gene': gene.strip(),
                'fitness': float(fitness)
            }

            is_initial_pool = len(buffer['gen']) == 1
            if is_initial_pool is False:
                if 'newone_idx' in tmpbuffer:
                    if tmpbuffer['newone_idx'] == cur_idx:
                        data['parents'] = tmpbuffer['parents']
                    else :
                        data['prev_num'] = cur_idx if cur_idx < tmpbuffer['newone_idx'] \
                            else cur_idx - 1
                else:
                    data['prev_num'] = cur_idx

            buffer['gen'][-1]['pool'].append(data)

            cur += 1
            

    return buffer

def parse_geqo_path(logs: list) -> dict:
    # _GENE_EXP = r'\[VPQO\]\[GEQO\]\[JOININFO\]((:? \d)*)'
    _GENE_EXP = r'\[VPQO\]\[GEQO\]\[JOININFO\]\ gene=((:? \d)*)'

    cur = 0
    buffer = {}

    while cur < len(logs):
        line = logs[cur].strip()
        print(cur, line)

        geneinfo = re.match(_GENE_EXP, line)
        if geneinfo is None:
            cur += 1
            continue

        gene = geneinfo.groups()[0].strip()
        if gene in buffer:
            cur += 1
            continue

        # reuse this
        _buf, _cur = parse_with_state_machine(logs, cur, '[VPQO][GEQO][JOININFO] gene=', '[VPQO][GEQO][JOININFO] Done')
        buffer[gene] = _buf
        cur = _cur

    return buffer


def get_geqo_data(log_lines: list) -> dict:
    data = parse_geqo_with_state_machine(log_lines)
    data['reloptinfo'] = parse_geqo_path(log_lines)
    return data

def split_log_lines(log_lines):
    _MARK = '[VPQO] split line'
    ret, for_items = [], []
    last = 0
    for idx, line in enumerate(log_lines):
        if _MARK not in line:
            continue

        ret.append(log_lines[last:idx])
        last = idx

        raw = line.split("RELOPTINFO")[1]
        relids = raw[raw.find("(")+1:raw.find(")")]
        for_items.append(relids)

    return ret, for_items

def process_log(log_lines):
    ret = {
        'type': 'dp',
        'base': [],
        'geqo': {},
        'dp': [] 
    }

    if '[GEQO]' in ''.join(log_lines):
        ret['type'] = 'geqo'

    _START_BASE_SIGN = '[VPQO][BASE] set_rel_pathlist started'
    _START_DP_SIGN = '[VPQO][DP] standard_join_search started'

    cur = 0
    # first pass for base and DP
    while cur < len(log_lines):
        line = log_lines[cur].strip()
        if _START_BASE_SIGN in line:
            base, _cur = get_base_path(log_lines, cur)
            ret['base'].append(base)
            cur = _cur - 1

        if _START_DP_SIGN in line:
            dp, _cur = get_dp_path(log_lines, cur)
            ret['dp'].append(dp)
            cur = _cur - 1
    
        cur += 1

    # second pass for GEQO
    if ret['type'] == 'geqo':
        ret['geqo'] = get_geqo_data(log_lines)
    
    return ret

def try_explain_analyze(in_query: str) -> str:
    hint_start, hint_end = in_query.find('/*+'), in_query.find('*/')
    hint, query = '', ''
    if hint_start != -1 and hint_end != -1:
        hint = in_query[hint_start:hint_end+2]
        query = in_query[hint_end+2:]
    else:
        query = in_query

    if 'explain' not in query.lower():
        query = 'EXPLAIN (ANALYZE true, VERBOSE true, FORMAT JSON) ' + query

    return hint + ' ' + query

        
class QueryView(APIView):
    def post(self, request, format=None):
        # SQL 공격이 근본적으로 가능하므로, 절대 링크를 외부공개 하지 마세요.
        q = request.data.get('query', 'EXPLAIN SELECT \'Hello World\'')
        d = request.data.get('db', 'postgres')
        q = try_explain_analyze(q)

        # Additional query to get server statistics
        _PG_CLASS_QUERY = 'SELECT relname, relpages, reltuples FROM pg_class;'
        
        # get query results
        try:
            conn = psycopg2.connect("host=localhost dbname={} user=postgres password=dbs402418".format(d))    # Connect to your postgres DB
            cur = conn.cursor()         # Open a cursor to perform database operations

            clear_previous_log()

            cur.execute(q)              # Execute a query
            records = cur.fetchall()    # Retrieve query results

            log_lines = read_and_clear_log()
            log_lines_list, for_items = split_log_lines(log_lines)
            ret = []
            for idx, logs in enumerate(log_lines_list):
                opt_data = process_log(logs)
                opt_data['for'] = for_items[idx]
                ret.append(opt_data)

            cur.execute(_PG_CLASS_QUERY)
            pg_class_results = cur.fetchall()

            # return
            return Response({'query': q, 'result': records, 'pg_class': pg_class_results, 'optimizer': ret})
        except psycopg2.OperationalError as e:
            print(e)
            return Response({'error': str(e)})
        except psycopg2.errors.SyntaxError as e:
            print(e)
            return Response({'error': str(e)})
        except psycopg2.errors.UndefinedTable as e:
            print(e)
            return Response({'error': str(e)})
        except psycopg2.ProgrammingError as e:
            print(e)
            return Response({'error': str(e)})