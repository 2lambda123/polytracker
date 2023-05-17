"""
The `clusters` command.
"""

from typing import (
    Generator,
    Tuple,
    List,
    Optional,
    Sequence,
    Union,
    TypeVar,
    FrozenSet,
    Generic,
    Dict,
    Iterable,
    Set,
)

from .disjoint_set import DisjointSet
from .plugins import Command
from .taint_dag import (
    TDFDHeader,
    TDFile,
    TDNode,
    TDRangeNode,
    TDSourceNode,
    TDSourceSection,
    TDUnionNode,
)
from pathlib import Path

from itertools import chain, product
from collections import defaultdict
import sys

from graphtage.matching import get_dtype
import numpy as np
from scipy.optimize import linear_sum_assignment
import networkx as nx
from tqdm import tqdm

T = TypeVar("T")


Label = int
ClusterID = int


def dfs(
    file: TDFile, label: Label
) -> Generator[Tuple[Label, TDNode], Optional[bool], None]:
    history: Set[Label] = set()
    stack: List[Label] = [label]
    while stack:
        label = stack.pop()
        if label in history:
            continue
        history.add(label)
        node = file.decode_node(label)
        expand_ancestors = yield label, node
        if expand_ancestors is None:
            expand_ancestors = True
        if isinstance(node, TDSourceNode):
            pass
        elif isinstance(node, TDUnionNode):
            if expand_ancestors:
                stack.append(min(node.left, node.right))
                stack.append(max(node.left, node.right))
        elif isinstance(node, TDRangeNode):
            if expand_ancestors:
                stack.extend(range(node.first, node.last + 1))
        else:
            raise NotImplementedError()


def cluster(
    file: TDFile, treat_nodes_affecting_control_flow_as_syncs: bool = True
) -> Dict[Tuple[Path, TDFDHeader], Dict[int, ClusterID]]:
    sources = list(file.read_fd_headers())
    sets: DisjointSet[int] = DisjointSet()
    source_labels: Dict[int, Dict[int, int]] = {i: {} for i in range(len(sources))}
    sinks = (s.label for s in file.sinks)
    num_sinks = file.num_sinks
    if treat_nodes_affecting_control_flow_as_syncs:
        sinks = chain(sinks, (label for label, _ in file.nodes_affecting_control_flow))
        num_sinks += len(file.nodes_affecting_control_flow)
    for sink_label in tqdm(
        sinks,
        total=num_sinks,
        desc="enumerating sinks",
        leave=False,
        unit="nodes",
    ):
        if sink_label in sets:
            continue
        cluster_id = sets.add(sink_label)
        with tqdm(
            desc="enumerating ancestors",
            unit="nodes",
            leave=False,
            total=file.label_count - 1 - len(sets),
            delay=1.0,
        ) as t:
            try:
                ancestors = dfs(file, sink_label)
                while True:
                    ancestor_label, node = next(ancestors)
                    t.update(1)
                    if isinstance(node, TDSourceNode):
                        if node.offset in source_labels[node.idx]:
                            # this source byte was read a separate time, so union our cluster with the preexisting
                            cluster_id = sets.union(
                                source_labels[node.idx][node.offset], cluster_id
                            )
                        source_labels[node.idx][node.offset] = cluster_id
                    if ancestor_label in sets and ancestor_label != sink_label:
                        # this ancestor is in another cluster
                        cluster_id = sets.union(cluster_id, ancestor_label)
                        ancestors.send(
                            False
                        )  # do not expand any of this node's ancestors
                    else:
                        new_cluster = sets.union(cluster_id, ancestor_label)
                        assert new_cluster == cluster_id
                        ancestors.send(True)
            except StopIteration:
                pass
    # Also add source bytes that affected control flow

    return {sources[source_idx]: labels for source_idx, labels in source_labels.items()}


def infinite_distance(s: Iterable[T], t: Iterable[T]) -> int:
    """Returns a distance that should be greater than any possibile edit distance between the two sequences"""
    return max(max(s), max(t)) ** 2


def ordered_edit_distance(
    s: Sequence[T], t: Sequence[T], infinite_cost: Optional[int] = None
) -> int:
    """Calculates the edit distance between two sequences.

    The sequences are assumed to be ordered, and T should support subtraction (e.g., like an int)
    """
    if infinite_cost is None:
        infinite_cost = infinite_distance(s, t)
    # if the sequences have no overlap, then they are an infinite distance apart
    if len(set(s) & set(t)) == 0:
        return infinite_cost
    distance: List[List[int]] = [[0] * (len(t) + 1) for _ in range(len(s) + 1)]
    for i in range(1, len(s) + 1):
        distance[i][0] = i
    for i in range(1, len(t) + 1):
        distance[0][i] = i
    for j in range(1, len(t) + 1):
        for i in range(1, len(s) + 1):
            if s[i - 1] == t[j - 1]:
                distance[i][j] = 0
            else:
                left_neighbor: Optional[T] = None
                if i - 2 >= 0:
                    left_neighbor = s[i - 2]
                right_neighbor: Optional[T] = None
                if i < len(s):
                    right_neighbor = s[i]
                if left_neighbor is None and right_neighbor is None:
                    deletion_cost = infinite_cost
                elif left_neighbor is None:
                    deletion_cost = right_neighbor - s[i - 1]
                elif right_neighbor is None:
                    deletion_cost = s[i - 1] - left_neighbor
                else:
                    assert right_neighbor > left_neighbor
                    deletion_cost = right_neighbor - left_neighbor
                if left_neighbor is not None and t[j - 1] < left_neighbor:
                    insertion_cost = infinite_cost
                    substitution_cost = infinite_cost
                elif right_neighbor is not None and t[j - 1] > right_neighbor:
                    insertion_cost = infinite_cost
                    substitution_cost = infinite_cost
                else:
                    substitution_cost = abs(s[i - 1] - t[j - 1])
                    insertion_cost = substitution_cost
                distance[i][j] = min(
                    distance[i - 1][j] + deletion_cost,  # deletion
                    distance[i][j - 1] + insertion_cost,  # insertion
                    distance[i - 1][j - 1] + substitution_cost,
                )
    return distance[len(s)][len(t)]


def clusters(g: nx.DiGraph, s: Dict[int, int]) -> Iterable[Iterable[int]]:
    sys.stderr.write(
        f"Calculating the weakly connected components of a {len(g)}-node graph...\n"
    )
    sys.stderr.flush()
    cs = nx.weakly_connected_components(g)

    cs = map(lambda x: x.intersection(s), cs)
    cs = map(lambda c: map(s.get, c), cs)
    return map(lambda x: sorted(list(x)), cs)

    return (
        sorted(list(x))
        for x in (
            map(s.get, c)
            for c in (
                y.intersection(s)
                for y in tqdm(
                    cs, desc="Clustering", unit="connected components", leave=False
                )
            )
        )
    )


def index_array(g: nx.DiGraph, s: Dict[int, int]) -> List[int]:
    ids = [-1] * (max(s.values()) + 1)
    for i, c in enumerate(clusters(g, s)):
        for o in c:
            ids[o] = i
    return ids


def dict_to_list(d: Dict[int, int], num_elements: Optional[int] = None) -> List[int]:
    if not d:
        return []
    max_key_value = max(d.keys()) + 1
    if num_elements is None:
        num_elements = max_key_value
    else:
        num_elements = max(num_elements, max_key_value)
    ids = [-1] * num_elements
    for offset, value in d.items():
        ids[offset] = value
    return ids


class IndexedSequence(Generic[T], Sequence[T]):
    def __init__(self, sequence: Sequence[T]):
        self.sequence: Tuple[T, ...] = tuple(sequence)
        self.indexes: Dict[T, List[int]] = {}
        for i, t in enumerate(
            tqdm(self.sequence, desc="Indexing", unit="elements", leave=False)
        ):
            if t in self.indexes:
                self.indexes[t].append(i)
            else:
                self.indexes[t] = [i]
        self.elements_by_index: Dict[Tuple[int, ...], T] = {
            tuple(indexes): label for label, indexes in self.indexes.items()
        }

    def __hash__(self):
        return hash(self.sequence)

    def __len__(self):
        return len(self.sequence)

    def __bool__(self):
        return bool(self.sequence)

    def __getitem__(self, index):
        return self.sequence[index]

    def __str__(self):
        return str(self.sequence)


class Matching(Generic[T]):
    def __init__(
        self,
        s1: Union[IndexedSequence[T], Sequence[T]],
        s2: Union[IndexedSequence[T], Sequence[T]],
        mapping: Dict[T, T],
    ):
        if isinstance(s1, IndexedSequence):
            self.s1: IndexedSequence[T] = s1
        else:
            self.s1 = IndexedSequence(s1)
        if isinstance(s2, IndexedSequence):
            self.s2: IndexedSequence[T] = s2
        else:
            self.s2 = IndexedSequence(s2)
        self.mapping: Dict[T, T] = dict(mapping)
        self.unmatched_s1: FrozenSet[T] = frozenset(
            self.s1.indexes.keys() - self.mapping.keys()
        )
        self.unmatched_s2: FrozenSet[T] = frozenset(
            self.s2.indexes.keys() - set(self.mapping.values())
        )
        self._edit_distance: Optional[int] = None
        self.infinite_cost: int = infinite_distance(
            range(len(self.s1)), range(len(self.s2))
        )

    @property
    def edit_distance(self) -> int:
        if self._edit_distance is not None:
            return self._edit_distance
        distance = sum(len(self.s1.indexes[u]) for u in self.unmatched_s1) + sum(
            len(self.s2.indexes[u]) for u in self.unmatched_s2
        )
        for s, t in self.mapping.items():
            distance += ordered_edit_distance(
                self.s1.indexes[s], self.s2.indexes[t], infinite_cost=self.infinite_cost
            )
        self._edit_distance = distance
        return distance

    @property
    def similarity(self) -> float:
        max_length = max(sum(self.s1), sum(self.s2))
        if max_length == 0:
            return 0.0
        return 1.0 - float(self.edit_distance) / float(max_length)

    def __str__(self):
        return (
            f"{self.mapping!s} cost={self.edit_distance}, similarity={self.similarity}"
        )


def match(s1: Sequence[T], s2: Sequence[T]) -> Matching[T]:
    if not isinstance(s1, IndexedSequence):
        sys.stderr.write("Indexing sequence 1...\n")
        sys.stderr.flush()
        s1 = IndexedSequence(s1)
    if not isinstance(s2, IndexedSequence):
        sys.stderr.write("Indexing sequence 2...\n")
        sys.stderr.flush()
        s2 = IndexedSequence(s2)
    # optimization: pre-pair any labels that are identical so we don't consider them in the matching:
    perfect_matchings: Dict[T, T] = {}
    perfectly_matched_indexes = (
        s1.elements_by_index.keys() & s2.elements_by_index.keys()
    )
    for matched_indexes in perfectly_matched_indexes:
        perfect_matchings[s1.elements_by_index[matched_indexes]] = s2.elements_by_index[
            matched_indexes
        ]
    sys.stderr.write(
        f"{len(perfectly_matched_indexes)} of {min(len(s1), len(s2))} bytes are perfect matchings!\n"
    )
    labels1 = [label for label in s1.indexes.keys() if label not in perfect_matchings]
    labels2 = [
        label
        for label, indexes in s2.indexes.items()
        if tuple(indexes) not in perfectly_matched_indexes
    ]
    sys.stderr.write(f"Building {len(labels1)}x{len(labels2)} weight matrix...\n")
    sys.stderr.flush()
    weights: List[List[int]] = [[0] * len(labels2) for _ in range(len(labels1))]
    min_edge: Optional[int] = None
    max_edge: Optional[int] = None
    infinite_cost = infinite_distance(range(len(s1)), range(len(s2)))
    for (i, l1), (j, l2) in tqdm(
        product(enumerate(labels1), enumerate(labels2)),
        desc="Calculating edit distances",
        leave=False,
        unit="pairs",
        total=len(labels1) * len(labels2),
    ):
        distance = ordered_edit_distance(
            s1.indexes[l1], s2.indexes[l2], infinite_cost=infinite_cost
        )
        weights[i][j] = distance
        if min_edge is None or min_edge > distance:
            min_edge = distance
        if max_edge is None or max_edge < distance:
            max_edge = distance
    dtype = get_dtype(min_edge, max_edge)
    sys.stderr.write("Calculating the minimum weight perfect matching...\n")
    sys.stderr.flush()
    left_matches = linear_sum_assignment(np.array(weights, dtype=dtype), maximize=False)
    perfect_matchings.update(
        {
            labels1[from_index]: labels2[to_index]
            for from_index, to_index in zip(*left_matches)
            if set(s1.indexes[labels1[from_index]]) & set(s2.indexes[labels2[to_index]])
            # only include matchings that have at least one index of overlap
        }
    )
    return Matching(
        s1,
        s2,
        mapping=perfect_matchings,
    )


class Clusters(Command):
    name = "clusters"
    help = "clusters input byte offsets based on their interaction"

    def __init_arguments__(self, parser):
        parser.add_argument(
            "trace_file",
            type=Path,
            nargs="?",
            help="print clusters for a trace file",
        )

        parser.add_argument(
            "-m",
            "--match",
            type=Path,
            nargs=2,
            help="print cluster matching for two trace files",
        )

    def to_graph(self, f: TDFile) -> Tuple[nx.DiGraph, Dict[int, int]]:
        graph = nx.DiGraph()
        sources: Dict[int, int] = dict()
        offsets: Dict[int, List[int]] = defaultdict(list)
        # Create graph from TDFile
        for label, node in tqdm(enumerate(f.nodes, start=1), total=f.label_count):
            graph.add_node(label)
            if isinstance(node, TDSourceNode):
                sources[label] = node.offset
                offsets[node.offset].append(label)
            elif isinstance(node, TDUnionNode):
                graph.add_edge(node.right, label)
                graph.add_edge(node.left, label)
            elif isinstance(node, TDRangeNode):
                for range_label in range(node.first, node.last + 1):
                    graph.add_edge(range_label, label)
            else:
                raise Exception("Unsupported node type")
        # Merge nodes that correspond to the same offset
        for ns in offsets.values():
            for n in ns[1:]:
                nx.contracted_nodes(graph, ns[0], n, copy=False)

        return graph, sources

    def run(self, args):
        def to_intervals(c: Iterable[int]) -> List[Tuple[int, int]]:
            r: List[Tuple[int, int]] = []
            for b in sorted(list(c)):
                if len(r) > 0 and b <= r[-1][1]:
                    continue
                e = b
                while e + 1 in c:
                    e += 1
                r.append((b, e))
            return r

        def print_intervals(i: Tuple[int, int]) -> str:
            return f"{i[0]} - {i[1]}"

        def graph_and_sources(path: Path) -> Tuple[nx.DiGraph, Dict[int, int]]:
            with open(path, "rb") as file:
                return self.to_graph(TDFile(file))

        if args.match:
            path1, path2 = args.match
            with open(path1, "rb") as file:
                clustering1 = cluster(TDFile(file))
            with open(path2, "rb") as file:
                clustering2 = cluster(TDFile(file))
            if not clustering1:
                sys.stderr.write(f"Error: {path1!s} has no taint data!\n")
                exit(1)
            elif not clustering2:
                sys.stderr.write(f"Error: {path2!s} has no taint data!\n")
                exit(1)
            sys.stderr.write(
                f"{path1!s} taint sources: {', '.join((str(p) for p, _ in clustering1.keys()))}\n"
            )
            sys.stderr.write(
                f"{path2!s} taint sources: {', '.join((str(p) for p, _ in clustering2.keys()))}\n"
            )
            if len(clustering1) > 1:
                sys.stderr.write(
                    f"Warning: {path1!s} has taint information from multiple input sources; using the largest\n"
                )
                taints = sorted(
                    [(len(offsets), source) for source, offsets in clustering1.items()]
                )
                for _, to_remove in taints[:-1]:
                    # remove all but the last (biggest) source:
                    del clustering1[to_remove]
            if len(clustering2) > 1:
                sys.stderr.write(
                    f"Warning: {path2!s} has taint information from multiple input sources; using the largest\n"
                )
                taints = sorted(
                    [(len(offsets), source) for source, offsets in clustering2.items()]
                )
                for _, to_remove in taints[:-1]:
                    # remove all but the last (biggest) source:
                    del clustering2[to_remove]
            (_, header), clustering = next(iter(clustering1.items()))
            if header.invalid_size():
                size: Optional[int] = None
            else:
                size = header.size
            index1 = dict_to_list(clustering, num_elements=size)
            del clustering1
            (_, header), clustering = next(iter(clustering2.items()))
            if header.invalid_size():
                size: Optional[int] = None
            else:
                size = header.size
            index2 = dict_to_list(clustering, num_elements=size)
            del clustering2
            sys.stderr.write("Matching...\n")
            m = match(index1, index2)
            print(f"{args.match[0]} -> {args.match[1]}")
            for k, v in m.mapping.items():
                indexes1 = m.s1.indexes[k]
                indexes2 = m.s2.indexes[v]
                cost = ordered_edit_distance(
                    indexes1, indexes2, infinite_cost=m.infinite_cost
                )
                if cost > 0:
                    print(f"{indexes1} -> {indexes2} cost {cost}")
            print(f"cost={m.edit_distance}, similarity={m.similarity}")
        else:
            for c in clusters(*graph_and_sources(args.trace_file)):
                print(list(map(print_intervals, to_intervals(c))))
