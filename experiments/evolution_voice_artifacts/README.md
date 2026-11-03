# Review artifacts: Evolution voice and Baileys

**HOLD. Neither patch is approved for production or complete.** These are
review artifacts for the third-party Evolution 2.3.7 source at commit
`cd800f2976e1e5b682fbf86a01ee4d85ae61f370`; they are not used by Beintaskbot
and this branch does not alter Evolution or any production service.

* `correlation-hold.patch` corresponds to local commit `4ad3c6a`. It contains
  the scoped reserved-ID experiment, offline tests and deployment/rollback
  plan. The actual correlation gate remains unconditionally disabled. Its
  synthetic bypass exists only in the test harness.
* `baileys-rc13-security-hold.patch` corresponds to local commit `41b6190`.
  It updates dependencies and proposes an isolated check of Baileys signature
  verification. It also moves the guard before database operations and removes
  database URL logging in the candidate. A separate `libsignal` initialization signature path remains
  uncorrected, alternative startup can bypass the guard, and the full runtime
  image and real provider behavior have not been established. Do not deploy.

Both patches are based on the same upstream commit and are **not** a tested
combined patch. They require independent security/compatibility review, a
verified provider-event provenance design, and the owner's decision before
any third-party repository or production Evolution change. Their local mock
tests are not evidence of WhatsApp delivery or of production exploitation.

To inspect in a throwaway checkout of that exact upstream commit, use
`git apply --check path/to/<patch>` before applying. Never apply either to a
production checkout or image as part of this artifact branch.
