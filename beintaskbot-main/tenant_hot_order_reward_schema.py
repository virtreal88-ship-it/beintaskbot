"""Additive migration: historical orders remain unpaid, tariff defaults to zero."""


def migrate_hot_order_rewards(cur) -> None:
    cur.execute('''ALTER TABLE saas_hot_orders ADD COLUMN IF NOT EXISTS reward_minor BIGINT NOT NULL
        DEFAULT 0 CHECK (reward_minor BETWEEN 0 AND 99999999999)''')
    # Composite company keys prevent references to another tenant's ledger/order.
    cur.execute('''ALTER TABLE saas_hot_orders ADD COLUMN IF NOT EXISTS reward_entry_id UUID NULL''')
    cur.execute('''ALTER TABLE saas_finance_entries ADD COLUMN IF NOT EXISTS hot_order_id UUID NULL''')
    cur.execute('''DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='saas_hot_order_reward_entry_fk'
            AND conrelid='saas_hot_orders'::regclass) THEN
            ALTER TABLE saas_hot_orders ADD CONSTRAINT saas_hot_order_reward_entry_fk
            FOREIGN KEY (tenant_id,reward_entry_id) REFERENCES saas_finance_entries(tenant_id,id) ON DELETE RESTRICT;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='saas_finance_hot_order_fk'
            AND conrelid='saas_finance_entries'::regclass) THEN
            ALTER TABLE saas_finance_entries ADD CONSTRAINT saas_finance_hot_order_fk
            FOREIGN KEY (tenant_id,hot_order_id) REFERENCES saas_hot_orders(tenant_id,id) ON DELETE RESTRICT;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='saas_finance_hot_reward_credit_check'
            AND conrelid='saas_finance_entries'::regclass) THEN
            ALTER TABLE saas_finance_entries ADD CONSTRAINT saas_finance_hot_reward_credit_check
            CHECK (hot_order_id IS NULL OR kind='credit');
        END IF;
    END $$''')
    cur.execute('''CREATE UNIQUE INDEX IF NOT EXISTS saas_finance_hot_order_reward_idx
        ON saas_finance_entries(tenant_id,hot_order_id) WHERE hot_order_id IS NOT NULL''')
