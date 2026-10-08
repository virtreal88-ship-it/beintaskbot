"""Bounded Linear reads and explicit task writes, using only a supplied tenant key."""
from tenant_linear_provider import query
from tenant_linear_policy import TenantLinearError

FIELDS = '''id identifier title description priority url createdAt updatedAt
  team { id } state { id name color type } project { id name }
  assignee { id name email }'''


def issue(key, issue_id):
    return query(key, 'query($id:String!) { issue(id:$id) { ' + FIELDS + ' } }', {'id': issue_id}).get('issue')


def issue_page(key, issue_filter, after=None):
    return query(key, 'query($filter:IssueFilter,$after:String) { issues(first:30,filter:$filter,after:$after,orderBy:createdAt) { nodes { '
        + FIELDS + ' } pageInfo { hasNextPage endCursor } } }', {'filter': issue_filter, 'after': after})['issues']


def team_members(key, team):
    result = query(key, '''query($id:String!) { team(id:$id) {
      members(first:100) { nodes { id name email } pageInfo { hasNextPage } }
    } }''', {'id': team}).get('team')
    if not result:
        raise TenantLinearError('Komanda tapılmadı.', 404)
    return result['members']


def mutate(key, issue_id, fields, *, create=False):
    operation, input_type = ('issueCreate', 'IssueCreateInput!') if create else ('issueUpdate', 'IssueUpdateInput!')
    if create:
        document = 'mutation($input:' + input_type + ') { issueCreate(input:$input) { success issue { id updatedAt } } }'
        variables = {'input': {'id': issue_id, **fields}}
    else:
        document = 'mutation($id:String!,$input:' + input_type + ') { issueUpdate(id:$id,input:$input) { success issue { id updatedAt } } }'
        variables = {'id': issue_id, 'input': fields}
    result = query(key, document, variables).get(operation) or {}
    if result.get('success') is not True or not result.get('issue'):
        raise TenantLinearError('Linear dəyişiklik təsdiqlənmədi.', 502)
    return result['issue']


def ensure_comment(key, comment_id, issue_id, body):
    try:
        result = query(key, '''mutation($input:CommentCreateInput!) {
          commentCreate(input:$input) { success comment { id } }
        }''', {'input': {'id': comment_id, 'issueId': issue_id, 'body': body}}).get('commentCreate') or {}
        if result.get('success') is True and (result.get('comment') or {}).get('id') == comment_id:
            return
    except TenantLinearError:
        pass
    # Reconcile a timeout/duplicate deterministic ID without adding a second comment.
    result = query(key, 'query($id:String!) { comment(id:$id) { id body issue { id } } }', {'id': comment_id}).get('comment')
    if not result or result['id'] != comment_id or result['body'] != body or result['issue']['id'] != issue_id:
        raise TenantLinearError('Səbəb Linear-da təsdiqlənmədi. Eyni sorğunu yenidən yoxlayın.', 502)
