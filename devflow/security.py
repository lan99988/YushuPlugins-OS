"""Keep publishing credentials out of development and build processes."""
import os
TOKEN_NAMES=('GH_TOKEN','GITHUB_TOKEN')
def non_gh_environment(source=None):
    env=dict(os.environ if source is None else source)
    return {k:v for k,v in env.items() if k.upper() not in TOKEN_NAMES}
def github_secrets(*environments):
    return {v for environment in environments if environment for k,v in environment.items() if k.upper() in TOKEN_NAMES and v}
def redact(value,secrets):
    for secret in sorted(secrets,key=len,reverse=True): value=value.replace(secret,'[REDACTED]')
    return value
