"""Greeting protocol over existing bounded conversation evidence, never CRM history."""
GREETING_RULE = (' Greeting policy: Use only the supplied chronological conversation. '
    'The current inbound burst consists of client messages after the most recent employee or manager reply. '
    'If this burst contains an explicit new client greeting not yet answered, acknowledge it briefly once, then answer the request. '
    'Earlier employee messages do not prohibit a new greeting. If the greeting was already answered or the current burst has no explicit greeting, '
    'do not repeat greetings or introductions. Do not add customer greetings to private employee explanations.')


def greeting_context(lines):
    """Indices into supplied evidence only; no greeting dictionary, new text or lookups."""
    start = 0
    for index, line in enumerate(lines):
        if line.get('speaker') in {'EMPLOYEE', 'SALES MANAGER', 'AGENT'}:
            start = index + 1
    return {'policy': 'current_inbound_burst_v1', 'start_index': start,
            'client_messages': sum(line.get('speaker') == 'CLIENT' for line in lines[start:])}
