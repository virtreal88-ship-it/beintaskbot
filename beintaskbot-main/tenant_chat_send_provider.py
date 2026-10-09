"""Single Kommo POST, no redirects or automatic OAuth retries after a write."""
import requests
from tenant_linear_policy import TenantLinearError


def send(credentials, route, text, wait_slot):
    wait_slot(credentials['account_domain'])
    try:
        response = requests.post(f"https://{credentials['account_domain']}/api/v4/talks/{route['talk_id']}/send_message",
            headers={'Authorization': 'Bearer ' + credentials['access_token'], 'Accept': 'application/json'},
            json={'text': text}, timeout=(5, 20), allow_redirects=False)
    except requests.RequestException:
        raise RuntimeError('Provider result unknown') from None
    if response.status_code in {400, 401, 402, 403, 404, 422, 429}:
        messages = {402: 'Kommo Chats API limiti və ya tarifini yoxlayın.',
                    403: 'Kommo inteqrasiyasının Sending to external chats icazəsi yoxdur.',
                    422: 'Kommo çatı bağlıdır.'}
        raise TenantLinearError(messages.get(response.status_code, 'Kommo mesajı qəbul etmədi. Bağlantını yoxlayın.'), 409)
    if response.status_code != 202:
        raise RuntimeError('Provider result unknown')
    result = response.json()
    if not isinstance(result, dict) or not isinstance(result.get('id'), str) or not result['id']:
        raise RuntimeError('Provider acceptance unknown')
    return result['id']
