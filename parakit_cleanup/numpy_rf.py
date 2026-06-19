"""
numpy_rf.py — DEPENDENCY-FREE Random-Forest inference for the detection-harness
cleanup post-passes (cymbal re-classifier + kick phantom-remover).

WHY THIS EXISTS
---------------
The cleanup pass needs to run a trained sklearn Pipeline(StandardScaler,
RandomForestClassifier) WITHOUT shipping sklearn / skl2onnx / onnxruntime /
joblib at inference time. ONNX was rejected: the standard ONNX TreeEnsemble op
spec uses FLOAT32 thresholds, which drifts the leaf-probabilities ~5e-3 and
FLIPS ~0.2% of cleanup decisions vs sklearn. This module instead exports
sklearn's NATIVE float64 thresholds and replicates sklearn's EXACT
float32-input-cast tree traversal, which is bit-exact with sklearn
(VERIFIED: max prob diff 0.00e+00, 0 decision flips over 1735 cymbal +
3089 kick onsets; apply_tree == sklearn tree.apply).

The ONLY runtime dependency is numpy.

FINITE-INPUT ASSUMPTION (NaN/inf)
---------------------------------
This traversal is bit-exact with sklearn ONLY for FINITE (NaN/inf-free) input.
sklearn's `_apply_dense` has a per-node `missing_go_to_left` branch taken BEFORE
the threshold test for NaN features; NumpyRF does NOT replicate it (a NaN here
evaluates `NaN <= thr` as False and ALWAYS goes right). The cleanup feature
extractors emit dense spectral descriptors that are never NaN/inf, so this never
bites — but the drop-in equivalence is NOT claimed for NaN input. `predict_proba`
asserts the input is finite so a violation fails loud rather than diverging
silently.

DROP-IN CONTRACT
----------------
NumpyRF exposes exactly the two attributes the post-passes touch:
  - .classes_        : np.ndarray of the INTEGER class labels (e.g. [0,1,2]),
                       matching sklearn RandomForestClassifier.classes_ order,
                       which is also the predict_proba column order.
  - .predict_proba(X): -> (n_samples, n_classes) float64 array.
So cymbal_postpass.assign(...) and kick_postpass.filter_kicks(...) /
phantom_proba(...) — which use only model.predict_proba(X) and model.classes_ —
accept a NumpyRF unchanged.

NPZ LAYOUT (pickle-free; np.load(..., allow_pickle=False))
----------------------------------------------------------
Scalars / small arrays:
  n_classes       : int  (as 0-d int array)
  n_trees         : int
  n_features      : int
  classes         : (n_classes,) int64   -> .classes_
  scaler_mean     : (n_features,) float64
  scaler_scale    : (n_features,) float64
  recommended_gate: (k,) float64 OR () float64  (informational; preserved)
  gate_keys       : (k,) <U... unicode  (present iff recommended_gate is a dict;
                    pairs by index with recommended_gate to reconstruct the dict)
Per-tree node arrays are CONCATENATED across all trees, indexed by `tree_offsets`
(an (n_trees+1,) int64 prefix-sum of per-tree node counts):
  feat            : (total_nodes,) int64    -> tree.feature
  thr             : (total_nodes,) float64  -> tree.threshold (NATIVE float64)
  left            : (total_nodes,) int64    -> tree.children_left  (-1 == leaf)
  right           : (total_nodes,) int64    -> tree.children_right
  leaf_proba      : (total_nodes, n_classes) float64
                    -> per-node normalized class distribution (only leaf rows are
                       read during traversal). sklearn tree.value is ALREADY a
                       normalized class distribution (class_weight baked in), so
                       this is tree.value[:, 0, :] taken AS-IS — NOT re-normalized.

This file imports NOTHING but numpy. No sklearn, no skl2onnx, no onnxruntime,
no joblib. Build the .npz with export_cleanup_npz.py (which DOES use sklearn +
joblib offline).
"""
from __future__ import annotations

import numpy as np


class NumpyRF:
    """Pure-numpy StandardScaler + RandomForestClassifier inference.

    Bit-exact with the source sklearn Pipeline because it (a) keeps sklearn's
    native float64 thresholds and (b) reproduces sklearn's traversal where the
    scaled feature row is cast to float32 before each `value <= threshold`
    comparison against the float64 threshold.
    """

    def __init__(self, classes, scaler_mean, scaler_scale, trees,
                 recommended_gate=None):
        # classes_ MUST be the integer label array in predict_proba column order.
        self.classes_ = np.asarray(classes, dtype=np.int64)
        self._mean = np.asarray(scaler_mean, dtype=np.float64)
        self._scale = np.asarray(scaler_scale, dtype=np.float64)
        # trees: list of (feat int64, thr float64, left int64, right int64,
        #                 leaf_proba float64 (n_nodes, n_classes))
        self._trees = trees
        self.n_classes_ = int(self.classes_.shape[0])
        self.recommended_gate = recommended_gate

    # -- traversal (replicates sklearn tree.apply under float32 input) ---------
    @staticmethod
    def _apply_tree(Xf32, feat, thr, left, right):
        """Vectorized tree descent; only non-leaf samples advance each step.

        Xf32 is the float32-cast, scaled feature matrix. The split test is
        `Xf32[i, feat[node]] <= thr[node]` with thr in float64 — exactly
        sklearn's semantics (float32 input value compared to the stored
        float64 threshold). Returns the leaf node index per sample.
        """
        n = Xf32.shape[0]
        node = np.zeros(n, dtype=np.int64)
        while True:
            leaf = left[node] == -1
            if leaf.all():
                break
            act = np.where(~leaf)[0]
            nd = node[act]
            go_left = Xf32[act, feat[nd]] <= thr[nd]
            node[act] = np.where(go_left, left[nd], right[nd])
        return node

    def predict_proba(self, X):
        """Return (n_samples, n_classes) class probabilities, float64.

        Scaling and the float32 cast match StandardScaler.transform followed by
        RandomForestClassifier.predict_proba: scale in float64, cast the scaled
        row to float32 for traversal, then average the per-tree leaf
        distributions.
        """
        X = np.asarray(X, dtype=np.float64)
        if X.ndim == 1:
            X = X.reshape(1, -1)
        # Bit-exactness holds only for finite input — NumpyRF does not replicate
        # sklearn's per-node missing_go_to_left NaN branch (see module docstring).
        # Fail loud rather than diverge silently if a NaN/inf ever slips in.
        if not np.isfinite(X).all():
            raise ValueError(
                "NumpyRF.predict_proba received non-finite (NaN/inf) input; "
                "this export is bit-exact with sklearn only for finite features.")
        Xf32 = ((X - self._mean) / self._scale).astype(np.float32)
        n = Xf32.shape[0]
        out = np.zeros((n, self.n_classes_), dtype=np.float64)
        for feat, thr, left, right, leaf_proba in self._trees:
            leaves = self._apply_tree(Xf32, feat, thr, left, right)
            out += leaf_proba[leaves]
        out /= len(self._trees)
        return out

    def predict(self, X):
        """argmax of predict_proba mapped back to the integer class labels.

        (The post-passes only use predict_proba + classes_; this is provided so
        NumpyRF is a fuller sklearn drop-in.)
        """
        proba = self.predict_proba(X)
        return self.classes_[np.argmax(proba, axis=1)]

    # -- construction from the pickle-free .npz -------------------------------
    @classmethod
    def load(cls, npz_path):
        """Reconstruct a NumpyRF from a .npz written by export_cleanup_npz.py.

        Uses allow_pickle=False — the layout is plain numeric/unicode arrays, so
        loading never executes pickle and pulls in no sklearn/joblib objects.
        """
        with np.load(npz_path, allow_pickle=False) as z:
            classes = z["classes"].astype(np.int64)
            scaler_mean = z["scaler_mean"].astype(np.float64)
            scaler_scale = z["scaler_scale"].astype(np.float64)

            n_trees = int(z["n_trees"])
            offsets = z["tree_offsets"].astype(np.int64)
            feat_all = z["feat"].astype(np.int64)
            thr_all = z["thr"].astype(np.float64)
            left_all = z["left"].astype(np.int64)
            right_all = z["right"].astype(np.int64)
            leaf_all = z["leaf_proba"].astype(np.float64)

            trees = []
            for i in range(n_trees):
                a, b = int(offsets[i]), int(offsets[i + 1])
                trees.append((
                    feat_all[a:b],
                    thr_all[a:b],
                    left_all[a:b],
                    right_all[a:b],
                    leaf_all[a:b],
                ))

            recommended_gate = cls._read_gate(z)

        return cls(classes, scaler_mean, scaler_scale, trees,
                   recommended_gate=recommended_gate)

    @staticmethod
    def _read_gate(z):
        """Recover recommended_gate from the npz without pickle.

        Stored as either a scalar float (kick: 0.9) or a dict reconstructed from
        parallel `gate_keys` (unicode) + `recommended_gate` (float) arrays
        (cymbal: {gate_to_ride:0.3, gate_swap:0.7}). Absent -> None.
        """
        if "recommended_gate" not in z.files:
            return None
        vals = z["recommended_gate"]
        if "gate_keys" in z.files:
            keys = [str(k) for k in z["gate_keys"]]
            flat = np.asarray(vals, dtype=np.float64).ravel()
            return {k: float(v) for k, v in zip(keys, flat)}
        arr = np.asarray(vals, dtype=np.float64)
        if arr.ndim == 0:
            return float(arr)
        if arr.size == 1:
            return float(arr.ravel()[0])
        return [float(v) for v in arr.ravel()]
