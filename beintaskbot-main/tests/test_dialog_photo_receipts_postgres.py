"""Repeat saved-photo history tests on native psycopg and disposable PostgreSQL."""
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db
from test_dialog_photo_receipts import test_history_reads_exact_visual_receipt_metadata_only
