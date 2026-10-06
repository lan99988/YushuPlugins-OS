"""Real CoreRuntime registry integration, with only staged test mock providers."""
import importlib
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from yushuos.config import load_config
from yushuos.deployment import lock_plugin
from yushuos.manifest import load_manifest
from yushuos.registry import provider_digest
from yushuos.runtime import CoreRuntime
from yushuos_sdk import StateStore

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('name', ['feishu', 'ima'])
def test_configure_tool_exists(name):
    assert (ROOT / 'plugins' / name / 'configure.py').is_file()


@pytest.fixture(params=['feishu', 'ima'])
def staged(request, tmp_path):
    app = importlib.import_module('plugins.' + request.param + '.adapter')
    assert (ROOT / 'plugins' / request.param / 'configure.py').is_file()
    helper = importlib.import_module('plugins.' + request.param + '.configure')
    source = tmp_path / 'source'
    source.mkdir()
    for name in ('adapter.py', 'definition.py', 'protocol.py', 'configure.py'):
        shutil.copy(ROOT / 'plugins' / request.param / name, source / name)
    # Test-only bootstrap is copied into an independently locked test fixture.
    response = ({'task': {'guid': 't', 'summary': 'mock task', 'tasklists': [{'tasklist_guid': 'tl'}]}}
                if app.APP == 'feishu' else {'code': 0, 'data': {'content': 'mock note'}})
    write_response = ({'message': {'message_id': 'mock-message'}} if app.APP == 'feishu'
                      else {'code': 0, 'data': {'note_id': 'mock-note'}})
    bootstrap = ('import json, os\nfrom pathlib import Path\n'
                 'from adapter import handle_envelope\nimport protocol\n'
                 f'RESPONSE = {response!r}\nWRITE_RESPONSE = {write_response!r}\n'
                 'finish = protocol.Receipts.finish\n'
                 'def handle(envelope):\n'
                 '    data = Path(envelope["context"]["data_path"])\n'
                 '    def mock(*args):\n'
                 '        if args[-1] is True:\n'
                 '            data.mkdir(parents=True, exist_ok=True)\n'
                 '            (data / "original-envelope.json").write_text(json.dumps(envelope), encoding="utf-8")\n'
                 '            with (data / "mock-submissions.txt").open("a", encoding="utf-8") as log: log.write("submitted\\n")\n'
                 '            return WRITE_RESPONSE\n'
                 '        return RESPONSE\n'
                 '    def crash_after_receipt(self, identity, out):\n'
                 '        finish(self, identity, out)\n'
                 '        os._exit(91)\n'
                 '    if envelope["request"]["request_id"] == "core-crash":\n'
                 '        protocol.Receipts.finish = crash_after_receipt\n'
                 '    return handle_envelope(envelope, transport=mock)\n'
                 'protocol.main(handle)\n')
    (source / 'run.py').write_text(bootstrap, encoding='utf-8')
    (source / 'plugin.yaml').write_text(yaml.safe_dump(app.MANIFEST), encoding='utf-8')
    assert lock_plugin(source)['verified']
    core = tmp_path / 'private-core'
    core.mkdir()
    ledger = core / 'state.sqlite3'
    with StateStore(ledger).connect(write=True):
        pass
    config = {'schema_version': 1, 'project_ref': 'demo', 'state': {'ledger_path': str(ledger)},
              'runtime': {'python_executable': sys.executable}, 'permissions': {'grants': [], 'denials': []},
              'bindings': {'resources': {'tasklist': 'tl', 'calendar': 'cal', 'recipient': 'chat', 'note': 'n', 'notebook': 'folder', 'kb': 'kb', 'account': 'self'}}}
    (core / 'config.yaml').write_text(yaml.safe_dump(config), encoding='utf-8')
    binding = tmp_path / 'account-binding.json'
    data = {'app': app.APP, 'configured': True, 'account_ref': 'self', 'credentials': {'client_id': 'FAKECLIENT', 'api_key': 'FAKEKEY'},
            'verified_capabilities': list(app.CAPABILITIES), 'authorized_capabilities': list(app.CAPABILITIES),
            'grants': [app.APP + '.read', app.APP + '.write']}
    binding.write_text(json.dumps(data), encoding='utf-8')
    return app, helper, source, core, binding


def read_request(app):
    return {'request_id': 'core-read', 'capability': 'feishu.task.get' if app.APP == 'feishu' else 'ima.note.get',
            'intent': 'query', 'project_ref': 'demo',
            'fields': {'tasklist_guid': 'tl', 'task_guid': 't'} if app.APP == 'feishu' else {'note_id': 'n'},
            'target': {'tasklist_guid': 'tl'} if app.APP == 'feishu' else {'note_id': 'n'}}


def grant_mock_host(core, app):
    path = core / 'config.yaml'
    config = yaml.safe_load(path.read_text(encoding='utf-8'))
    config['permissions']['grants'] = [app.APP + '.read', app.APP + '.write']
    path.write_text(yaml.safe_dump(config), encoding='utf-8')


def write_request(app, request_id='core-write'):
    return {'request_id': request_id, 'capability': 'feishu.message.send' if app.APP == 'feishu' else 'ima.note.create',
            'intent': 'command', 'project_ref': 'demo',
            'fields': {'receive_id': 'chat', 'receive_id_type': 'chat_id', 'text': 'mock'} if app.APP == 'feishu' else {'folder_id': 'folder', 'content': 'mock'},
            'target': {'receive_id': 'chat'} if app.APP == 'feishu' else {'folder_id': 'folder'}}


def test_configure_native_pointer_and_hashes_without_grants(staged):
    app, helper, source, core, binding = staged
    out = helper.configure(core, plugin_root=source, binding_file=binding)
    assert out['status'] == 'succeeded'
    config = load_config(core)
    assert config['permissions']['grants'] == []
    assert config['plugins']['config'][app.PLUGIN_ID]['binding_file'] == str(binding)
    pointer = json.loads(Path(config['bindings']['apps'][app.PLUGIN_ID]['active_pointer']).read_text(encoding='utf-8'))
    assert pointer['app'] == app.PLUGIN_ID
    assert pointer['config_file'] == str(binding)
    assert pointer['ledger_path'] == config['state']['ledger_path']
    spec = load_manifest(core / 'plugins' / app.PLUGIN_ID / '0.1.0' / 'plugin.yaml')
    assert len(provider_digest(spec, config)) == 64
    runtime = CoreRuntime(core)
    assert 'permission_not_granted' in runtime.registry.unavailable_reasons(read_request(app)['capability'])
    refused = runtime.invoke(read_request(app))
    assert refused.status == 'unavailable'
    assert 'permission_not_granted' in refused.error['reasons']


def test_python_executable_link_resolves_without_relaxing_private_paths(staged, tmp_path):
    _, helper, source, core, binding = staged
    executable = tmp_path / 'python-link'
    try:
        executable.symlink_to(Path(sys.executable).resolve())
    except OSError:
        pytest.skip('OS does not permit this test user to create file symlinks')
    config_path = core / 'config.yaml'
    config = yaml.safe_load(config_path.read_text(encoding='utf-8'))
    config['runtime']['python_executable'] = str(executable)
    config_path.write_text(yaml.safe_dump(config), encoding='utf-8')
    helper.configure(core, plugin_root=source, binding_file=binding)
    loaded = load_config(core)
    pointer = Path(next(iter(loaded['bindings']['apps'].values()))['active_pointer'])
    assert json.loads(pointer.read_text(encoding='utf-8'))['python_executable'] == str(Path(sys.executable).resolve())
    with pytest.raises(ValueError, match='linked_path'):
        helper._ordinary(executable)


def test_real_core_runtime_read_and_unverified_membership(staged):
    app, helper, source, core, binding = staged
    helper.configure(core, plugin_root=source, binding_file=binding)
    grant_mock_host(core, app)
    runtime = CoreRuntime(core)
    assert runtime.registry.resolve(read_request(app)['capability']) is not None
    out = runtime.invoke(read_request(app))
    assert out.status == 'succeeded'
    assert out.data['entity']['id'] == ('t' if app.APP == 'feishu' else 'n')
    data = json.loads(binding.read_text(encoding='utf-8'))
    data['verified_capabilities'] = []
    binding.write_text(json.dumps(data), encoding='utf-8')
    out = CoreRuntime(core).invoke(read_request(app))
    assert out.status == 'unavailable' and out.error['code'] == 'not_verified'


def test_real_core_write_preview_and_field_scope_gate(staged):
    app, helper, source, core, binding = staged
    helper.configure(core, plugin_root=source, binding_file=binding)
    grant_mock_host(core, app)
    request = write_request(app, 'core-preview')
    out = CoreRuntime(core).invoke(request)
    assert out.status == 'preview'
    assert not (core / 'plugin-data').exists()
    request = read_request(app)
    scope = 'tasklist_guid' if app.APP == 'feishu' else 'note_id'
    request['target'][scope] = 'unauthorized'
    assert CoreRuntime(core).invoke(request).status == 'unavailable'
    request['target'][scope] = 'tl' if app.APP == 'feishu' else 'n'
    request['fields'][scope] = 'unauthorized'
    out = CoreRuntime(core).invoke(request)
    assert out.status == 'failed' and out.error['code'] == 'permission_denied'


def test_true_core_missing_app_binding_is_unavailable(staged):
    app, helper, source, core, binding = staged
    from yushuos.deployment import install_plugin
    install_plugin(source, core)
    grant_mock_host(core, app)
    runtime = CoreRuntime(core)
    assert runtime.invoke(read_request(app)).status == 'unavailable'
    assert 'app_binding_missing' in runtime.registry.unavailable_reasons(read_request(app)['capability'])


def test_config_file_changes_native_provider_digest(staged):
    app, helper, source, core, binding = staged
    helper.configure(core, plugin_root=source, binding_file=binding)
    config = load_config(core)
    spec = load_manifest(core / 'plugins' / app.PLUGIN_ID / '0.1.0' / 'plugin.yaml')
    before = provider_digest(spec, config)
    data = json.loads(binding.read_text(encoding='utf-8'))
    data['credentials']['api_key'] = 'OTHERFAKE'
    binding.write_text(json.dumps(data), encoding='utf-8')
    assert provider_digest(spec, config) != before


def test_configure_rejects_repository_or_missing_ledger(staged, tmp_path):
    app, helper, source, core, binding = staged
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    with pytest.raises(ValueError):
        helper.configure(repo / 'core', plugin_root=source, binding_file=binding)
    (core / 'state.sqlite3').unlink()
    with pytest.raises(ValueError):
        helper.configure(core, plugin_root=source, binding_file=binding)
    assert not (core / 'plugins').exists()


def test_configure_default_binding_is_unconfigured(staged):
    app, helper, source, core, binding = staged
    out = helper.configure(core, plugin_root=source)
    config = json.loads(Path(out['binding_file']).read_text(encoding='utf-8'))
    assert config['configured'] is False
    assert config['authorized_capabilities'] == config['verified_capabilities'] == config['grants'] == []


def test_real_core_external_write_repeated_id_reads_private_receipt(staged):
    app, helper, source, core, binding = staged
    helper.configure(core, plugin_root=source, binding_file=binding)
    grant_mock_host(core, app)
    request = write_request(app)
    first = CoreRuntime(core).invoke(request, mode='execute', host_mode='execute')
    assert first.status == 'succeeded'
    assert first.data['provider_id'] == ('mock-message' if app.APP == 'feishu' else 'mock-note')
    second = CoreRuntime(core).invoke(request, mode='execute', host_mode='execute')
    assert second.to_dict() == first.to_dict()
    private_data = core / 'plugin-data' / app.PLUGIN_ID / 'data'
    assert (private_data / 'mock-submissions.txt').read_text(encoding='utf-8').splitlines() == ['submitted']
    assert (private_data / 'external-receipts.sqlite3').is_file()


def test_crash_after_private_receipt_core_resume_and_readonly_recovery_cli(staged):
    app, helper, source, core, binding = staged
    helper.configure(core, plugin_root=source, binding_file=binding)
    grant_mock_host(core, app)
    request = write_request(app, 'core-crash')
    first = CoreRuntime(core).invoke(request, mode='execute', host_mode='execute')
    assert first.status == 'unknown'
    runtime = CoreRuntime(core)
    assert runtime.state.operation_context(request['request_id'])
    assert runtime.state.effective_receipt(request['request_id']) is None
    resumed = runtime.resume(request, host_mode='execute')
    assert resumed.status == 'unavailable'
    assert '找不到可恢复的原请求收据' in resumed.message
    private_data = core / 'plugin-data' / app.PLUGIN_ID / 'data'
    with sqlite3.connect(private_data / 'external-receipts.sqlite3') as db:
        assert db.execute('SELECT state FROM receipts').fetchone()[0] == 'confirmed'
    envelope = json.loads((private_data / 'original-envelope.json').read_text(encoding='utf-8'))
    envelope['action'] = 'recover'
    # The test-only run.py injects fake transport even if a regression resubmits.
    process = subprocess.run([sys.executable, str(core / 'plugins' / app.PLUGIN_ID / '0.1.0' / 'run.py')],
                             input=json.dumps(envelope) + '\n', capture_output=True, text=True, encoding='utf-8', check=True)
    recovered = json.loads(process.stdout)
    assert recovered['status'] == 'succeeded' and recovered['data']['operation_status'] == 'confirmed'
    assert (private_data / 'mock-submissions.txt').read_text(encoding='utf-8').splitlines() == ['submitted']
    # Direct receipt recovery must not fabricate or mutate a Core confirmation.
    assert runtime.state.effective_receipt(request['request_id']) is None


@pytest.mark.parametrize('membership,code', [('authorized_capabilities', 'not_authorized'), ('grants', 'permission_denied')])
def test_real_core_account_membership_refuses_write_without_submission(staged, membership, code):
    app, helper, source, core, binding = staged
    helper.configure(core, plugin_root=source, binding_file=binding)
    grant_mock_host(core, app)
    data = json.loads(binding.read_text(encoding='utf-8'))
    data[membership] = []
    binding.write_text(json.dumps(data), encoding='utf-8')
    out = CoreRuntime(core).invoke(write_request(app), mode='execute', host_mode='execute')
    assert out.status == 'unavailable' and out.error['code'] == code
    assert not list((core / 'plugin-data').rglob('external-receipts.sqlite3'))
    assert not list((core / 'plugin-data').rglob('mock-submissions.txt'))


def test_manifest_mock_verification_does_not_enable_unimplemented_capabilities(staged):
    app, helper, source, core, binding = staged
    for cap in app.MANIFEST['capabilities']:
        assert cap['authorized'] == cap['verified'] == cap['implemented']
    helper.configure(core, plugin_root=source, binding_file=binding)
    grant_mock_host(core, app)
    runtime = CoreRuntime(core)
    for cap in app.MANIFEST['capabilities']:
        if not cap['implemented']:
            assert runtime.registry.resolve(cap['name']) is None
            assert 'not_implemented' in runtime.registry.unavailable_reasons(cap['name'])
