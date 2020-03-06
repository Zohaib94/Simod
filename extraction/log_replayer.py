# -*- coding: utf-8 -*-
import networkx as nx
import pandas as pd

from support_modules import support as sup
# import support as sup

from collections import OrderedDict


class LogReplayer():
    """
    This class replays an evant log over a model,
    measures the global conformance and the KPI's related with times
    """

    def __init__(self, model, log, settings, source='log', run_num=0):
        """constructor"""
        self.source = source
        self.run_num = run_num
        self.one_timestamp = settings['read_options']['one_timestamp']
        self.model = model
        self.m_data = pd.DataFrame.from_dict(dict(model.nodes.data()),
                                             orient='index')
        self.start_tasks_list = list()
        self.end_tasks_list = list()
        self.find_start_finish_tasks()

        self.subsec_set = set()
        self.create_subsec_set()

        self.parallel_gt_exec = list()
        self.parallel_execution_list()

        self.not_conformant_traces = list()
        self.conformant_traces = list()
        self.process_stats = list()
        self.traces = log.get_traces()

        self.replay()

    def replay(self) -> None:
        """
        Replays the event-log traces over the BPMN model
        """
        for index in range(0, len(self.traces)):
            t_times = list()
            trace = self.traces[index][1:-1]  # remove start and end event
            # Check if is a complete trace
            current_node = self.find_task_node(self.model,
                                               trace[0]['task'])
            last_node = self.find_task_node(self.model,
                                            trace[-1]['task'])
            if current_node not in self.start_tasks_list:
                self.not_conformant_traces.append(trace)
                continue
            if last_node not in self.end_tasks_list:
                self.not_conformant_traces.append(trace)
                continue
            # Initialize
            temp_gt_exec = self.parallel_gt_exec
            cursor = [current_node]
            remove = True
            is_conformant = True
            # ----time recording------
            t_times = self.save_record(t_times, trace, 0)
            # ------------------------
            for i in range(1, len(trace)):
                nnode = self.find_task_node(self.model, trace[i]['task'])
                # If loop management
                if nnode == cursor[-1]:
                    t_times = self.save_record(t_times, trace, i, nnode)
                    self.model.node[nnode]['executions'] += 1
                    continue
                try:
                    cursor, pnode = self.update_cursor(nnode, self.model, cursor)
                    # ----time recording------
                    t_times = self.save_record(t_times, trace, i, pnode)
                    self.model.node[nnode]['executions'] += 1
                    # ------------------------
                except:
                    is_conformant = False
                    break
                for element in reversed(cursor[:-1]):
                    element_type = self.model.node[element]['type']
                    # Process AND
                    if element_type == 'gate3':
                        gate = [d for d in temp_gt_exec if d['nod_num'] == element][0]
                        gate.update({'executed': gate['executed'] + 1})
                        if gate['executed'] < gate['num_paths']:
                            remove = False
                        else:
                            remove = True
                            cursor.remove(element)
                    # Process Task
                    elif element_type == 'task':
                        if (element, nnode) in self.subsec_set and remove:
                            cursor.remove(element)
                    # Process other
                    elif remove:
                        cursor.remove(element)
            if is_conformant:
                # Append the original one
                self.conformant_traces.extend(self.traces[index])
                self.process_stats.extend(t_times)
            else:
                self.not_conformant_traces.extend(trace)
            sup.print_progress(((index / (len(self.traces) - 1)) * 100),
                               'Replaying process traces ')
        self.calculate_process_metrics()
        sup.print_done_task()

    @staticmethod
    def update_cursor(nnode: int, model: iter, cursor: list) -> (list, int):
        """
        This method updates the execution pile (cursor) in the replay

        Parameters
        ----------
        nnode : int
        model : iter
        cursor : list

        Raises
        ------
        Exception
            Incoherent path.

        Returns
        -------
        (list, int)
            cursor and past node.
        """
        tasks = list(filter(
            lambda x: model.node[x]['type'] == 'task', cursor))
        shortest_path = list()
        pnode = 0
        for pnode in reversed(tasks):
            try:
                shortest_path = list(nx.shortest_path(model,
                                                      pnode,
                                                      nnode))[1:]
                pnode = pnode
                break
            except nx.NetworkXNoPath:
                pass
        if len(list(filter(lambda x: model.node[x]['type'] == 'task',
                           shortest_path))) > 1:
            raise Exception('Incoherent path')
        ap_list = cursor + shortest_path
        # Preserve order and leave only new
        cursor = list(OrderedDict.fromkeys(ap_list))
        return cursor, pnode

# =============================================================================
# metrics related
# =============================================================================

    def save_record(self, t_times: list, trace: list, i: int, node=None) -> list:
        """
        Saves the execution times of the trace in the t_times list

        Parameters
        ----------
        t_times : list
        trace : list
        i : int
        node : TYPE, optional

        Returns
        -------
        list
        """
        prev_rec = dict()
        if node is not None:
            task = self.model.node[node]['name']
            for x in t_times[::-1]:
                if task == x['task']:
                    prev_rec = x
                    break
        record = self.create_record(trace, i, self.one_timestamp, prev_rec)
        if record['resource'] != 'AUTO':
            t_times.append(record)
        return t_times

    @staticmethod
    def create_record(trace, index, one_timestamp, last_event=dict()):
        if not bool(last_event):
            enabling_time = trace[index]['end_timestamp']
        else:
            enabling_time = last_event['end_timestamp']
        record = {'caseid': trace[index]['caseid'],
                  'task': trace[index]['task'],
                  'end_timestamp': trace[index]['end_timestamp'],
                  'enable_timestamp': enabling_time,
                  'resource': trace[index]['user']}
        if not one_timestamp:
            record['start_timestamp'] = trace[index]['start_timestamp']
        return record

    def calculate_process_metrics(self):
        ps = pd.DataFrame(self.process_stats)
        ps = ps[~ps.task.isin(['Start', 'End'])]
        ps = ps[ps.resource != 'AUTO']
        ps['source'] = self.source
        ps['run_num'] = self.run_num
        if self.one_timestamp:
            ps['duration'] = ps['end_timestamp'] - ps['enable_timestamp']
            ps['duration'] = ps['duration'].dt.total_seconds()
        else:
            ps = ps.to_dict('records')
            for record in ps:
                duration = (record['end_timestamp'] -
                            record['start_timestamp']).total_seconds()
                waiting = (record['start_timestamp'] -
                           record['enable_timestamp']).total_seconds()
                multitask = 0
                # TODO check resourse for multi_tasking
                if waiting < 0:
                    waiting = 0
                    if record['end_timestamp'] > record['enable_timestamp']:
                        duration = (record['end_timestamp'] -
                                    record['enable_timestamp']).total_seconds()
                        multitask = (record['enable_timestamp'] -
                                     record['start_timestamp']).total_seconds()
                    else:
                        multitask = duration
                record['processing_time'] = duration
                record['waiting_time'] = waiting
                record['multitasking'] = multitask
            ps = pd.DataFrame(ps)
        self.process_stats = ps

# =============================================================================
# Initial methods
# =============================================================================

    def find_start_finish_tasks(self) -> None:
        m_data = self.m_data.copy()
        start_node = m_data[m_data.type == 'start'].index.tolist()[0]
        end_node = m_data[m_data.type == 'end'].index.tolist()[0]
        self.start_tasks_list = sup.reduce_list(
            self.find_next_tasks(self.model,
                                 start_node))
        self.end_tasks_list = sup.reduce_list(
            self.find_next_tasks(self.model.reverse(copy=True),
                                 end_node))

    def create_subsec_set(self) -> None:
        m_data = self.m_data.copy()
        task_list = m_data[m_data.type == 'task'].index.tolist()

        for task in task_list:
            next_tasks = sup.reduce_list(
                self.find_next_tasks(self.model, task))
            for n_task in next_tasks:
                self.subsec_set.add((task, n_task))

    def parallel_execution_list(self) -> None:
        m_data = self.m_data.copy()
        para_gates = m_data[m_data.type == 'gate3'].index.tolist()
        for x in para_gates:
            self.parallel_gt_exec.append(
                {'nod_num': x,
                 'num_paths': len(list(self.model.neighbors(x))),
                 'executed': 0})

# =============================================================================
# Support methods
# =============================================================================

    @staticmethod
    def find_next_tasks(model: iter, num: int) -> list:
        tasks_list = list()
        for node in model.neighbors(num):
            if model.node[node]['type'] in ['task', 'start', 'end']:
                tasks_list.append([node])
            else:
                tasks_list.append(
                    LogReplayer.find_next_tasks(model, node))
        return tasks_list

    @staticmethod
    def find_task_node(model: iter, task_name: str) -> int:
        resp = list(filter(
            lambda x: model.node[x]['name'] == task_name, model.nodes))
        if len(resp) > 0:
            resp = resp[0]
        else:
            raise Exception('Task not found on bpmn structure...')
        return resp
