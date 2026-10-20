APP_TITLE = 'ws 996打包工具 By.白鲨 v0.115'
IMPORT_PLACEHOLDER = '请输入 MirServer 目录地址'
MAX_EXPORT_PRESETS = 20
SAFE_SETUP_FILENAME = '!Setup.txt'
ADMIN_LIST_REL_PATH = 'Mir200/Envir/AdminList.txt'
FORCE_HIDDEN_EXCLUDE_NAMES = {'globalval.ini'}
# !Setup.txt no longer globally hidden; if covered by pack/rule/check, use safe template.
SETUP_PROTECTED_NAMES = {'!setup.txt'}
FULL_PACKAGE_INCLUDE_ROOTS = ('DBServer', 'Mir200')
FULL_PACKAGE_EXCLUDE_DIRS = {
    'DBServer/DBServer.log',
    'DBServer/FDB',
    'DBServer/Log',
    'Mir200/Log',
}
FULL_PACKAGE_EXCLUDE_PATHS = {
    'DBServer/DBServer.map',
    'Mir200/M2Server.map',
}
FULL_PACKAGE_EXCLUDE_NAMES = {'globalval.ini'}
FULL_PACKAGE_EXCLUDE_SUFFIXES = (
    '.exe', '.dll', '.elf', '.json', '.code-snippets',
    '.tmp', '.temp', '.bak', '.backup', '.old', '.orig', '.swp', '.swo', '.log', '.pdb',
)
