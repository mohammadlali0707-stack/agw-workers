#!/usr/bin/env python3
"""tests/test_check_handover.py - Unit tests for Handover Gate & Squash Reconciliation

Covers 13 test cases verifying:
1. Detached HEAD detection & fallback
2. Symbolic ref branch detection
3. Shallow repository detection
4. Direct ancestor reachability pass
5. Unreachable commit standard fail
6. Squash-merge reconciliation by tree SHA
7. Squash-merge reconciliation by PR subject reference
8. Squash-merge reconciliation by git patch-id
9. Squash-merge grandfathered commit exemption (e.g. fd3f68d)
10. Infrastructure vs verification error classification
11. MAP.md line & byte ceiling enforcement
12. CHANGELOG.md unrecorded commit detection
13. CLI invocation end-to-end execution
"""

import os
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from Tools.check_handover import (
    check_changelog_recording,
    check_map_size,
    check_squash_merge_reconciliation,
    classify_failure,
    get_current_branch,
    is_shallow_repository,
    main,
    verify_commit_reachable,
)


class TestCheckHandover(unittest.TestCase):
    """13 Comprehensive unit tests for Tools/check_handover.py."""

    # 1. Detached HEAD detection & fallback
    @patch("Tools.check_handover.run_git_cmd")
    def test_detached_head_detection_and_fallback(self, mock_git):
        # symbolic-ref fails on detached HEAD
        mock_git.side_effect = [
            (128, "", "fatal: ref HEAD is not a symbolic ref"),
            (0, "a1b2c3d4e5f67890", ""),
        ]
        with patch.dict(os.environ, {"GITHUB_HEAD_REF": "feature-branch"}):
            info = get_current_branch()
            self.assertTrue(info["detached"])
            self.assertEqual(info["type"], "detached_head")
            self.assertEqual(info["name"], "feature-branch")
            self.assertEqual(info["sha"], "a1b2c3d4e5f67890")

    # 2. Symbolic ref branch detection
    @patch("Tools.check_handover.run_git_cmd")
    def test_symbolic_ref_branch_detection(self, mock_git):
        mock_git.return_value = (0, "main", "")
        info = get_current_branch()
        self.assertFalse(info["detached"])
        self.assertEqual(info["type"], "branch")
        self.assertEqual(info["name"], "main")

    # 3. Shallow repository detection
    @patch("Tools.check_handover.run_git_cmd")
    def test_shallow_repository_detection(self, mock_git):
        mock_git.return_value = (0, "true", "")
        self.assertTrue(is_shallow_repository())
        mock_git.return_value = (0, "false", "")
        self.assertFalse(is_shallow_repository())

    # 4. Direct ancestor reachability pass
    @patch("Tools.check_handover.run_git_cmd")
    def test_direct_ancestor_reachability_pass(self, mock_git):
        # rev-parse target succeeds, merge-base succeeds (rc=0)
        mock_git.side_effect = [
            (0, "origin/main", ""),
            (0, "", ""),
        ]
        res = verify_commit_reachable("1122334455667788", target_branch="main")
        self.assertTrue(res["reachable"])
        self.assertEqual(res["method"], "direct_ancestor")

    # 5. Unreachable commit standard fail
    @patch("Tools.check_handover.check_squash_merge_reconciliation")
    @patch("Tools.check_handover.run_git_cmd")
    def test_unreachable_commit_standard_fail(self, mock_git, mock_reconcile):
        mock_git.side_effect = [
            (0, "origin/main", ""),
            (1, "", "not an ancestor"),
        ]
        mock_reconcile.return_value = (False, "Squash-merge reconciliation failed")
        res = verify_commit_reachable("deadbeef99999999", target_branch="main")
        self.assertFalse(res["reachable"])
        self.assertEqual(res["method"], "unreachable")

    # 6. Squash-merge reconciliation by tree SHA
    @patch("Tools.check_handover.run_git_cmd")
    def test_squash_merge_reconciliation_by_tree_sha(self, mock_git):
        tree_sha = "abcdef0123456789"
        mock_git.side_effect = [
            # Strategy 2 log check
            (0, "other commit message", ""),
            # Strategy 3 rev-parse tree
            (0, tree_sha, ""),
            # Strategy 3 log trees
            (0, f"different_tree\n{tree_sha}\nanother_tree", ""),
        ]
        reconciled, details = check_squash_merge_reconciliation("9988776655443322")
        self.assertTrue(reconciled)
        self.assertIn("tree SHA match", details)

    # 7. Squash-merge reconciliation by PR subject reference
    @patch("Tools.check_handover.run_git_cmd")
    def test_squash_merge_reconciliation_by_pr_subject(self, mock_git):
        target_sha = "1234567890abcdef"
        mock_git.side_effect = [
            (0, f"c0ffee1 feat: squash commit containing {target_sha[:7]} (#42)", ""),
        ]
        reconciled, details = check_squash_merge_reconciliation(target_sha)
        self.assertTrue(reconciled)
        self.assertIn("target branch commit message reference", details)

    # 8. Squash-merge reconciliation by git patch-id
    @patch("subprocess.run")
    @patch("Tools.check_handover.run_git_cmd")
    def test_squash_merge_reconciliation_by_patch_id(self, mock_git, mock_subproc):
        commit_sha = "aabbccddeeff0011"
        mock_git.side_effect = [
            (0, "unrelated log", ""),          # log
            (1, "", "tree error"),             # tree rev-parse fail
            (0, "diff --git a/f b/f ...", ""),  # show commit
            (0, "diff on main ...", ""),       # log -p
        ]
        # mock patch-id executions
        mock_p1 = MagicMock(returncode=0, stdout="pid_hash_12345 commit_a\n")
        mock_p2 = MagicMock(returncode=0, stdout="pid_hash_12345 commit_b\npid_other commit_c\n")
        mock_subproc.side_effect = [mock_p1, mock_p2]

        reconciled, details = check_squash_merge_reconciliation(commit_sha)
        self.assertTrue(reconciled)
        self.assertIn("git patch-id equivalence", details)

    # 9. Squash-merge grandfathered commit exemption
    def test_squash_merge_grandfathered_commit(self):
        reconciled, details = check_squash_merge_reconciliation("fd3f68d")
        self.assertTrue(reconciled)
        self.assertIn("grandfathered squash list", details)

        reconciled_full, _ = check_squash_merge_reconciliation("fd3f68df9f2e7b3e1c6b1b22e9a3b610c4d29193")
        self.assertTrue(reconciled_full)

    # 10. Infrastructure vs verification error classification
    def test_infrastructure_vs_verification_error_classification(self):
        infra_res = classify_failure("git", "fatal: ref HEAD is not a symbolic ref", 128)
        self.assertEqual(infra_res["classification"], "INFRASTRUCTURE_LIMIT")
        self.assertIn("Detached HEAD", infra_res["root_cause"])

        shallow_res = classify_failure("git", "fatal: shallow update not allowed", 128)
        self.assertEqual(shallow_res["classification"], "INFRASTRUCTURE_LIMIT")
        self.assertIn("Shallow clone", shallow_res["root_cause"])

        verif_res = classify_failure("gate", "commit fd3f68d is not an ancestor", 1)
        self.assertEqual(verif_res["classification"], "VERIFICATION_ERROR")
        self.assertIn("Unrecorded commit", verif_res["root_cause"])

    # 11. MAP.md line & byte ceiling enforcement
    def test_map_size_ceiling_enforcement(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("Line\n" * 10)
            map_path = f.name
        try:
            ok, _ = check_map_size(map_path, max_lines=20, max_bytes=1000)
            self.assertTrue(ok)
            over_lines, msg = check_map_size(map_path, max_lines=5, max_bytes=1000)
            self.assertFalse(over_lines)
            self.assertIn("MAP ceiling exceeded", msg)
        finally:
            os.remove(map_path)

    # 12. CHANGELOG.md unrecorded commit detection
    def test_changelog_unrecorded_commit_detection(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("# Changelog\n- fd3f68d: initial squash merge fix\n")
            cl_path = f.name
        try:
            ok, missing = check_changelog_recording(cl_path, ["fd3f68d12345"])
            self.assertTrue(ok)
            self.assertEqual(missing, [])

            ok_fail, missing_fail = check_changelog_recording(cl_path, ["unrecorded9999"])
            self.assertFalse(ok_fail)
            self.assertIn("unrecorded9999", missing_fail)
        finally:
            os.remove(cl_path)

    # 13. CLI invocation end-to-end execution
    @patch("Tools.check_handover.verify_commit_reachable")
    @patch("sys.argv", ["check_handover.py", "--commit", "fd3f68d"])
    def test_cli_invocation_end_to_end(self, mock_reach):
        mock_reach.return_value = {
            "reachable": True,
            "method": "squash_merge_reconciled",
            "commit": "fd3f68d",
            "details": "Reconciled via grandfathered squash list: fd3f68d",
        }
        ret = main()
        self.assertEqual(ret, 0)


if __name__ == "__main__":
    unittest.main()
