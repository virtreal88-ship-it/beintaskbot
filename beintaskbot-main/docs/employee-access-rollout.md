# Employee access rollout and rollback

The employee-access schema changes are additive: new profile/session tables and columns are introduced, existing identity and membership rows are retained, and no user data is deleted. Roll back by reverting the application release while leaving these tables and columns in place; do not run a destructive down migration. Existing Telegram identities and signed legacy sessions remain accepted until their normal expiry, while newly issued browser sessions can be revoked through the session registry.
