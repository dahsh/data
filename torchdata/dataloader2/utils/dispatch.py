# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

#  from multiprocessing.queues import Queue
from typing import Dict, List, Optional, Set

from torchdata.dataloader2.graph import DataPipe, DataPipeGraph, list_dps, traverse_dps

from torchdata.datapipes.iter import IterDataPipe, ShardingRoundRobinDispatcher


__all__ = ["_DummyIterDataPipe", "find_lca_non_replicable_dp", "find_replicable_branches"]


class _DummyIterDataPipe(IterDataPipe):
    r"""
    This DataPipe is a placeholder to be replaced by the ``QueueWrapper``
    that connects the worker process for non-replicable DataPipe.
    """
    # TODO: Revert `_DummyIterDataPipe` as the placeholder when `_SerializationWrapper`
    #       can handle mp.Queue. See: https://github.com/pytorch/data/issues/934
    #  req_queue: Queue
    #  res_queue: Queue


def find_lca_non_replicable_dp(graph: DataPipeGraph) -> Optional[DataPipe]:
    r"""
    Given the graph of DataPipe generated by ``traverse_dps`` function, return the
    DataPipe instance that is the lowest common ancestor of all non-replicable DataPipes
    (all parent DataPipes of ``sharding_round_robin_dispatch``)

    Note:
      - If multiple branches share the same source DataPipe and any branch contains a
        non-replicable DataPipe, the lowest common ancestor of all branches is returned.
      - If there is any non-replicable DataPipe in a circular-referenced (sub)graph, the
        whole (sub)graph is treated as non-replicable and the last DataPipe is returned.
    """
    assert len(graph) == 1, "DataPipeGraph should only contain a single output DataPipe"

    def _is_round_robin_sharding(dp: DataPipe) -> bool:
        return type(dp) == ShardingRoundRobinDispatcher

    dps = list_dps(graph)
    non_replicable_dps: Set[int] = set()
    for dp in dps:
        # Skip when it has been visited
        if id(dp) in non_replicable_dps:
            continue
        if _is_round_robin_sharding(dp):
            parent_dps = list_dps(traverse_dps(dp))
            for par_dp in parent_dps:
                non_replicable_dps.add(id(par_dp))

    root_dp_id = list(graph.keys())[0]
    root_dp, root_graph = graph[root_dp_id]

    lca_for_subgraph: Dict[int, Optional[DataPipe]] = {}

    def _get_lca_from_graph(root_dp_id, root_dp, root_graph) -> Optional[DataPipe]:  # pyre-ignore
        if root_dp_id in lca_for_subgraph:
            return lca_for_subgraph[root_dp_id]
        if root_dp_id in non_replicable_dps:
            lca_for_subgraph[root_dp_id] = root_dp
            return root_dp
        lca_for_subgraph[root_dp_id] = None
        non_replicable_parents = []
        for dp_id, (dp, src_graph) in root_graph.items():
            res = _get_lca_from_graph(dp_id, dp, src_graph)
            if res is not None:
                non_replicable_parents.append(res)
        # `root_dp` becomes the lowest common ancestor of this branch,
        # if there are more than one unique non-replicable DataPipe prior to it.
        if len(non_replicable_parents) > 0:
            # One unique non-replicable DataPipe
            if len(non_replicable_parents) == 1 or all(
                dp == non_replicable_parents[0] for dp in non_replicable_parents
            ):
                lca_for_subgraph[root_dp_id] = non_replicable_parents[0]
            # Multiple non-replicable DataPipes
            else:
                lca_for_subgraph[root_dp_id] = root_dp
        return lca_for_subgraph[root_dp_id]

    return _get_lca_from_graph(root_dp_id, root_dp, root_graph)


def find_replicable_branches(graph: DataPipeGraph) -> List[DataPipe]:
    r"""
    Given the graph of DataPipe generated by ``traverse_dps`` function, return the DataPipe
    instances that don't have ``_DummyIterDataPipe`` (non-replicable DataPipe) in the prior graph.
    """
    assert len(graph) == 1, "DataPipeGraph should only contain a single output DataPipe"

    dps: List[DataPipe] = []
    branch_is_replicable: Dict[int, bool] = {}

    root_dp_id = list(graph.keys())[0]
    root_dp, root_graph = graph[root_dp_id]

    def _is_replicable_graph(root_dp_id, root_dp, root_graph) -> bool:  # pyre-ignore
        if root_dp_id in branch_is_replicable:
            return branch_is_replicable[root_dp_id]
        if type(root_dp) == _DummyIterDataPipe:
            branch_is_replicable[root_dp_id] = False
            return False
        branch_is_replicable[root_dp_id] = True
        for dp_id, (dp, src_graph) in root_graph.items():
            if not _is_replicable_graph(dp_id, dp, src_graph):
                branch_is_replicable[root_dp_id] = False
                #  Do not break to go through all children
        if not branch_is_replicable[root_dp_id]:
            # All children should have been added to branch_is_replicable already
            for dp_id, (dp, _) in root_graph.items():
                if branch_is_replicable[dp_id]:
                    dps.append(dp)
        return branch_is_replicable[root_dp_id]

    if _is_replicable_graph(root_dp_id, root_dp, root_graph):
        dps.append(root_dp)

    return dps
