"""Offline policy tests; persistence, ACL, identity and AI are not exercised."""
import unittest

from tenant_customer_memory_policy import Decision, Snapshot, assess_automatic


S = Snapshot(memory_version=3, source_version='source-v1', identity_version='identity-v1')


def assess(**changes):
    args = dict(started=S, current=S, deleted_source=False,
                existing_value=None, proposed_value='Uses Android')
    args.update(changes)
    return assess_automatic(**args)


class CustomerMemoryPolicyTests(unittest.TestCase):
    def test_new_proposal_requires_review_and_repetition_is_not_confirmation(self):
        self.assertEqual(assess(), Decision.CREATE_REVIEW)
        self.assertEqual(assess(existing_value='Uses Android'), Decision.UNCHANGED)

    def test_conflicts_never_replace_manual_or_automatic_fact(self):
        self.assertEqual(assess(existing_value='Uses iOS'), Decision.REVIEW_CONFLICT)
        self.assertEqual(assess(existing_value='Uses iOS', manually_edited=True), Decision.REVIEW_CONFLICT)

    def test_deletion_suppresses_original_source(self):
        self.assertEqual(assess(deleted_source=True), Decision.SUPPRESSED)

    def test_changed_memory_source_or_identity_invalidates_inflight_work(self):
        for changed in (Snapshot(4, 'source-v1', 'identity-v1'),
                        Snapshot(3, 'source-v2', 'identity-v1'),
                        Snapshot(3, 'source-v1', 'identity-v2')):
            with self.subTest(changed=changed):
                self.assertEqual(assess(current=changed), Decision.STALE_WORK)
                self.assertEqual(assess(current=changed, deleted_source=True), Decision.STALE_WORK)

    def test_empty_or_mistyped_input_fails_closed(self):
        with self.assertRaises(ValueError):
            assess(proposed_value='  ')
        with self.assertRaises(TypeError):
            assess(deleted_source=1)


if __name__ == '__main__':
    unittest.main()
