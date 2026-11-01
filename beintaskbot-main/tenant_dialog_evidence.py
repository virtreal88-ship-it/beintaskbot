"""Source attribution for dialog evidence; no identity inference or media I/O."""


def speaker(row):
    # Safe server-produced group projection; raw webhook data is removed there.
    if row.get('_evidence_speaker') in {'CLIENT', 'EMPLOYEE', 'ASSISTANT'}:
        return row['_evidence_speaker']
    raw = row.get('raw') if isinstance(row.get('raw'), dict) else {}
    if raw.get('ai_authored') is True:
        return 'ASSISTANT'
    if row.get('direction') == 'incoming' and raw.get('verified_employee_id') is None:
        return 'CLIENT'
    return 'EMPLOYEE'


def caption(row):
    if str(row.get('message_type') or '').lower() not in {'image', 'video', 'document'}:
        return ''
    text = str(row.get('body') or '').strip()
    return ('[Sender caption; statement, not extracted media evidence] ' + text) if text else ''


def provenance(row):
    kind = str(row.get('message_type') or 'text').lower()
    origin = {'CLIENT': 'customer_statement', 'EMPLOYEE': 'employee_statement_unverified',
              'ASSISTANT': 'prior_ai_statement_unverified'}[speaker(row)]
    result = {'statement_origin': origin}
    if kind not in {'', 'text'}:
        result.update(observation_origin='saved_media_observation_untrusted',
                      application_identity='only_the_window_explicitly_identified_in_this_observation',
                      relationship_to_latest_request='must_be_supported_by_customer_followup')
        text = str(row.get('_copilot_attachment_text') or row.get('media_evidence_context') or '')
        if '[phone]' in text:
            result['numeric_identifiers'] = 'redacted_unknown_type; do_not_infer_customer_did_not_supply_them'
    return result


GROUNDING_RULE = ''' Treat each source's provenance separately. Customer statements describe reported symptoms;
employee and prior AI statements are not verified diagnoses. A saved photo observation describes only its own
image/window: quote license wording accurately and attribute it only to the visibly identified application.
A free/non-professional license is not evidence of expiration; a remote-support application's license says
nothing about another application's license. If application identity is absent or ambiguous, say it is unknown.
Use historical product evidence only when the current customer follow-up refers to that issue; do not transfer
it to a distinct new topic. Before asking for product identity, check the supplied captions and observations.
Do not request an already supplied readable product name or ask to resend all media. Redacted or unreadable
identifiers are unknown, not absent: clarify only the missing field, without guessing or recovering digits.
Differences between POS and administrator visibility do not establish product creation, registration,
warehouse or synchronization mechanisms. Assert a cause or mechanism only with applicable verified company
knowledge and evidence that its stated conditions hold; otherwise describe the symptom and the specific check
still needed. Never turn a staff hypothesis or a conditional knowledge suggestion into a case fact.'''
