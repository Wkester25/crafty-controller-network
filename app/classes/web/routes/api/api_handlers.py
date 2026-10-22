from app.classes.web.routes.api.index_handler import ApiIndexHandler
from app.classes.web.routes.api.jsonschema import (
    ApiJsonSchemaHandler,
    ApiJsonSchemaListHandler,
)
from app.classes.web.routes.api.not_found import ApiNotFoundHandler
from app.classes.web.routes.api.auth.invalidate_tokens import (
    ApiAuthInvalidateTokensHandler,
)
from app.classes.web.routes.api.auth.login import ApiAuthLoginHandler
from app.classes.web.routes.api.auth.passkey import (
    ApiAuthPasskeyLoginOptionsHandler,
    ApiAuthPasskeyLoginVerifyHandler,
)
from app.classes.web.routes.api.roles.index import ApiRolesIndexHandler
from app.classes.web.routes.api.roles.role.index import ApiRolesRoleIndexHandler
from app.classes.web.routes.api.roles.role.servers import ApiRolesRoleServersHandler
from app.classes.web.routes.api.roles.role.users import ApiRolesRoleUsersHandler

from app.classes.web.routes.api.servers.index import ApiServersIndexHandler
from app.classes.web.routes.api.servers.server.action import (
    ApiServersServerActionHandler,
)
from app.classes.web.routes.api.servers.server.index import (
    ApiServersServerIndexHandler,
    ApiServersServerUpdateConfig,
)
from app.classes.web.routes.api.servers.server.logs import (
    ApiServersServerLogFilesHandler,
    ApiServersServerLogsHandler,
)
from app.classes.web.routes.api.servers.server.public import (
    ApiServersServerPublicHandler,
)
from app.classes.web.routes.api.servers.server.status import (
    ApiServersServerStatusHandler,
)
from app.classes.web.routes.api.servers.server.stats import ApiServersServerStatsHandler
from app.classes.web.routes.api.servers.server.history import (
    ApiServersServerHistoryHandler,
)
from app.classes.web.routes.api.servers.server.stdin import ApiServersServerStdinHandler
from app.classes.web.routes.api.servers.server.tasks.index import (
    ApiServersServerTasksIndexHandler,
)
from app.classes.web.routes.api.servers.server.backups.index import (
    ApiServersServerBackupsIndexHandler,
)
from app.classes.web.routes.api.servers.server.backups.backup.index import (
    ApiServersServerBackupsBackupIndexHandler,
    ApiServersServerBackupsBackupFilesIndexHandler,
)
from app.classes.web.routes.api.servers.server.backups.backup.download import (
    ApiServersServerBackupsBackupDownloadHandler,
)
from app.classes.web.routes.api.servers.server.files import (
    ApiServersServerFilesIndexHandler,
    ApiServersServerFilesCreateHandler,
    ApiServersServerFilesZipHandler,
    ApiServersServerFileDownload,
    ApiServersServerFilesOperationHandler,
)
from app.classes.web.routes.api.servers.server.tasks.task.children import (
    ApiServersServerTasksTaskChildrenHandler,
)
from app.classes.web.routes.api.servers.server.tasks.task.index import (
    ApiServersServerTasksTaskIndexHandler,
)
from app.classes.web.routes.api.servers.server.tasks.task.run import (
    ApiServersServerTasksTaskRunHandler,
)
from app.classes.web.routes.api.servers.server.webhooks.index import (
    ApiServersServerWebhooksIndexHandler,
)
from app.classes.web.routes.api.servers.server.webhooks.webhook.index import (
    ApiServersServerWebhooksManagementIndexHandler,
)
from app.classes.web.routes.api.servers.server.users import ApiServersServerUsersHandler
from app.classes.web.routes.api.servers.server.upload import ApiServerFilesUpload
from app.classes.web.routes.api.users.index import ApiUsersIndexHandler
from app.classes.web.routes.api.users.user.index import ApiUsersUserIndexHandler
from app.classes.web.routes.api.users.user.permissions import (
    ApiUsersUserPermissionsHandler,
)
from app.classes.web.routes.api.users.user.otp import (
    APIUsersTOTPHandler,
    APIUsersTOTPIndexHandler,
    APIUsersTOTPRecovery,
    APIUsersTOTPVerifyIndexHandler,
)
from app.classes.web.routes.api.users.user.passkey import (
    ApiUsersPasskeyIndexHandler,
    ApiUsersPasskeyVerifyHandler,
    ApiUsersPasskeyHandler,
)
from app.classes.web.routes.api.users.user.api import ApiUsersUserKeyHandler
from app.classes.web.routes.api.users.user.pfp import ApiUsersUserPfpHandler
from app.classes.web.routes.api.users.user.public import ApiUsersUserPublicHandler
from app.classes.web.routes.api.crafty.announcements.index import (
    ApiAnnounceIndexHandler,
)
from app.classes.web.routes.api.crafty.check import ApiCraftyCheck
from app.classes.web.routes.api.crafty.network import (
    ApiCraftyNetworkHandler,
    ApiCraftyNetworkActionHandler,
)
from app.classes.web.routes.api.crafty.config.index import (
    ApiCraftyConfigIndexHandler,
    ApiCraftyCustomizeIndexHandler,
    ApiCraftyEmbedIndexHandler,
    ApiCraftyLogosIndexHandler,
)
from app.classes.web.routes.api.crafty.config.server_dir import (
    ApiCraftyConfigServerDirHandler,
)
from app.classes.web.routes.api.crafty.stats.stats import ApiCraftyHostStatsHandler
from app.classes.web.routes.api.crafty.clogs.index import ApiCraftyLogIndexHandler
from app.classes.web.routes.api.crafty.clogs.support import ApiCraftySupportIndexHandler
from app.classes.web.routes.api.crafty.imports.index import ApiImportFilesIndexHandler
from app.classes.web.routes.api.crafty.imports.upload import APIServerImportUpload
from app.classes.web.routes.api.crafty.config.upload import (
    APICraftyCustomizeUpload,
    APICraftyEmbedUpload,
    APICraftyLogoFullUpload,
    APICraftyLogoSquareUpload,
)
from app.classes.web.routes.api.crafty.exe_cache import (
    ApiCraftyJarCacheIndexHandler,
    ApiCraftySteamCacheIndexHandler,
)
from app.classes.web.routes.api.crafty.antilockout.index import ApiCraftyLockoutHandler
from app.classes.web.routes.api.servers.server.stop_all import (
    ApiServersServerStopAllHandler,
)


def api_handlers(handler_args):
    return [
        # Auth routes
        (
            r"/api/v2/auth/login/?",
            ApiAuthLoginHandler,
            handler_args,
        ),
        (
            r"/api/v2/auth/invalidate_tokens/?",
            ApiAuthInvalidateTokensHandler,
            handler_args,
        ),
        (
            r"/api/v2/auth/passkey/login/options/?",
            ApiAuthPasskeyLoginOptionsHandler,
            handler_args,
        ),
        (
            r"/api/v2/auth/passkey/login/verify/?",
            ApiAuthPasskeyLoginVerifyHandler,
            handler_args,
        ),
        (
            r"/api/v2/crafty/resetPass/?",
            ApiCraftyLockoutHandler,
            handler_args,
        ),
        (
            r"/api/v2/crafty/announcements/?",
            ApiAnnounceIndexHandler,
            handler_args,
        ),
        (
            r"/api/v2/crafty/check/?",
            ApiCraftyCheck,
            handler_args,
        ),
        (
            r"/api/v2/crafty/config/?",
            ApiCraftyConfigIndexHandler,
            handler_args,
        ),
        (
            r"/api/v2/crafty/config/customize/?",
            ApiCraftyCustomizeIndexHandler,
            handler_args,
        ),
        (
            r"/api/v2/crafty/config/embed/?",
            ApiCraftyEmbedIndexHandler,
            handler_args,
        ),
        (
            r"/api/v2/crafty/config/logos/?",
            ApiCraftyLogosIndexHandler,
            handler_args,
        ),
        (
            r"/api/v2/crafty/config/servers_dir/?",
            ApiCraftyConfigServerDirHandler,
            handler_args,
        ),
        (
            r"/api/v2/crafty/network/?",
            ApiCraftyNetworkHandler,
            handler_args,
        ),
        (
            r"/api/v2/crafty/network/action/([a-z_]+)/?",
            ApiCraftyNetworkActionHandler,
            handler_args,
        ),
        (
            r"/api/v2/crafty/stats/?",
            ApiCraftyHostStatsHandler,
            handler_args,
        ),
        (
            r"/api/v2/crafty/logs/([a-z0-9_]+)/?",
            ApiCraftyLogIndexHandler,
            handler_args,
        ),
        (
            r"/api/v2/crafty/JarCache(?:/([0-9-]+))?",
            ApiCraftyJarCacheIndexHandler,
            handler_args,
        ),
        (
            r"/api/v2/import/archive/select?",
            ApiImportFilesIndexHandler,
            handler_args,
        ),
        # User routes
        (
            r"/api/v2/users/?",
            ApiUsersIndexHandler,
            handler_args,
        ),
        (
            r"/api/v2/users/([0-9]+)/key/?",
            ApiUsersUserKeyHandler,
            handler_args,
        ),
        (
            r"/api/v2/users/([0-9]+)/key/([0-9]+)/?",
            ApiUsersUserKeyHandler,
            handler_args,
        ),
        (
            r"/api/v2/users/([0-9]+)/?",
            ApiUsersUserIndexHandler,
            handler_args,
        ),
        (
            r"/api/v2/users/(@me)/?",
            ApiUsersUserIndexHandler,
            handler_args,
        ),
        (
            r"/api/v2/users/([0-9]+)/totp/([a-z0-9-]+)/verify/?",
            APIUsersTOTPVerifyIndexHandler,
            handler_args,
        ),
        (
            r"/api/v2/users/(@me)/totp/([a-z0-9-]+)/verify/?",
            APIUsersTOTPVerifyIndexHandler,
            handler_args,
        ),
        (
            r"/api/v2/users/(@me)/totp/recovery/renew/?",
            APIUsersTOTPRecovery,
            handler_args,
        ),
        (
            r"/api/v2/users/([0-9]+)/totp/recovery/renew/?",
            APIUsersTOTPRecovery,
            handler_args,
        ),
        (
            r"/api/v2/users/([0-9]+)/totp/?",
            APIUsersTOTPIndexHandler,
            handler_args,
        ),
        (
            r"/api/v2/users/([0-9]+)/totp/?",
            APIUsersTOTPIndexHandler,
            handler_args,
        ),
        (
            r"/api/v2/users/([0-9]+)/totp/([a-z0-9-]+)/?",
            APIUsersTOTPHandler,
            handler_args,
        ),
        (
            r"/api/v2/users/(@me)/totp/([a-z0-9-]+)/?",
            APIUsersTOTPHandler,
            handler_args,
        ),
        (
            r"/api/v2/users/([0-9]+)/passkeys/?",
            ApiUsersPasskeyIndexHandler,
            handler_args,
        ),
        (
            r"/api/v2/users/(@me)/passkeys/?",
            ApiUsersPasskeyIndexHandler,
            handler_args,
        ),
        (
            r"/api/v2/users/([0-9]+)/passkeys/([a-z0-9-]+)/verify/?",
            ApiUsersPasskeyVerifyHandler,
            handler_args,
        ),
        (
            r"/api/v2/users/(@me)/passkeys/([a-z0-9-]+)/verify/?",
            ApiUsersPasskeyVerifyHandler,
            handler_args,
        ),
        (
            r"/api/v2/users/([0-9]+)/passkeys/([a-z0-9-]+)/?",
            ApiUsersPasskeyHandler,
            handler_args,
        ),
        (
            r"/api/v2/users/(@me)/passkeys/([a-z0-9-]+)/?",
            ApiUsersPasskeyHandler,
            handler_args,
        ),
        (
            r"/api/v2/users/([0-9]+)/permissions/?",
            ApiUsersUserPermissionsHandler,
            handler_args,
        ),
        (
            r"/api/v2/users/(@me)/permissions/?",
            ApiUsersUserPermissionsHandler,
            handler_args,
        ),
        (
            r"/api/v2/users/([0-9]+)/pfp/?",
            ApiUsersUserPfpHandler,
            handler_args,
        ),
        (
            r"/api/v2/users/(@me)/pfp/?",
            ApiUsersUserPfpHandler,
            handler_args,
        ),
        (
            r"/api/v2/users/([0-9]+)/public/?",
            ApiUsersUserPublicHandler,
            handler_args,
        ),
        (
            r"/api/v2/users/(@me)/public/?",
            ApiUsersUserPublicHandler,
            handler_args,
        ),
        # Server routes
        (
            r"/api/v2/servers/?",
            ApiServersIndexHandler,
            handler_args,
        ),
        (
            r"/api/v2/crafty/JarCache/?",
            ApiCraftyJarCacheIndexHandler,
            handler_args,
        ),
        (
            r"/api/v2/crafty/SteamCache/?",
            ApiCraftySteamCacheIndexHandler,
            handler_args,
        ),
        (
            r"/api/v2/servers/status/?",
            ApiServersServerStatusHandler,
            handler_args,
        ),
        (
            r"/api/v2/servers/stop_all/?",
            ApiServersServerStopAllHandler,
            handler_args,
        ),
        (
            r"/api/v2/servers/([a-z0-9-]+)/update/config/?",
            ApiServersServerUpdateConfig,
            handler_args,
        ),
        (
            r"/api/v2/servers/([a-z0-9-]+)/?",
            ApiServersServerIndexHandler,
            handler_args,
        ),
        (
            r"/api/v2/servers/([a-z0-9-]+)/backups/?",
            ApiServersServerBackupsIndexHandler,
            handler_args,
        ),
        (
            r"/api/v2/servers/([a-z0-9-]+)/backups/backup/([a-z0-9-]+)/download/(.+)/?",
            ApiServersServerBackupsBackupDownloadHandler,
            handler_args,
        ),
        (
            r"/api/v2/servers/([a-z0-9-]+)/backups/backup/([a-z0-9-]+)/?",
            ApiServersServerBackupsBackupIndexHandler,
            handler_args,
        ),
        (
            r"/api/v2/servers/([a-z0-9-]+)/backups/backup/([a-z0-9-]+)/files/?",
            ApiServersServerBackupsBackupFilesIndexHandler,
            handler_args,
        ),
        (
            r"/api/v2/servers/([a-z0-9-]+)/files/create/?",
            ApiServersServerFilesCreateHandler,
            handler_args,
        ),
        (
            r"/api/v2/servers/([a-z0-9-]+)/files/zip/?",
            ApiServersServerFilesZipHandler,
            handler_args,
        ),
        (
            r"/api/v2/crafty/admin/upload/embed/?",
            APICraftyEmbedUpload,
            handler_args,
        ),
        (
            r"/api/v2/crafty/admin/upload/logo_full/?",
            APICraftyLogoFullUpload,
            handler_args,
        ),
        (
            r"/api/v2/crafty/admin/upload/logo_square/?",
            APICraftyLogoSquareUpload,
            handler_args,
        ),
        (
            r"/api/v2/crafty/admin/upload/?",
            APICraftyCustomizeUpload,
            handler_args,
        ),
        (
            r"/api/v2/crafty/support_logs/?",
            ApiCraftySupportIndexHandler,
            handler_args,
        ),
        (
            r"/api/v2/servers/import/upload/?",
            APIServerImportUpload,
            handler_args,
        ),
        (
            r"/api/v2/servers/([a-z0-9-]+)/files/upload/?",
            ApiServerFilesUpload,
            handler_args,
        ),
        (
            r"/api/v2/servers/([a-z0-9-]+)/files/(move|copy)/?$",
            ApiServersServerFilesOperationHandler,
            handler_args,
        ),
        (
            r"/api/v2/servers/([a-z0-9-]+)/files(?:/([a-zA-Z0-9-]+))?/?",
            ApiServersServerFilesIndexHandler,
            handler_args,
        ),
        (
            r"/api/v2/servers/([a-z0-9-]+)/files/(.+)/download/?$",
            ApiServersServerFileDownload,
            handler_args,
        ),
        (
            r"/api/v2/servers/([a-z0-9-]+)/tasks/?",
            ApiServersServerTasksIndexHandler,
            handler_args,
        ),
        (
            r"/api/v2/servers/([a-z0-9-]+)/tasks/([0-9]+)/?",
            ApiServersServerTasksTaskIndexHandler,
            handler_args,
        ),
        (
            r"/api/v2/servers/([a-z0-9-]+)/tasks/([0-9]+)/children/?",
            ApiServersServerTasksTaskChildrenHandler,
            handler_args,
        ),
        (
            r"/api/v2/servers/([a-z0-9-]+)/tasks/([0-9]+)/run/?",
            ApiServersServerTasksTaskRunHandler,
            handler_args,
        ),
        (
            r"/api/v2/servers/([a-z0-9-]+)/stats/?",
            ApiServersServerStatsHandler,
            handler_args,
        ),
        (
            r"/api/v2/servers/([a-z0-9-]+)/history/?",
            ApiServersServerHistoryHandler,
            handler_args,
        ),
        (
            r"/api/v2/servers/([a-z0-9-]+)/webhook/([0-9]+)/?",
            ApiServersServerWebhooksManagementIndexHandler,
            handler_args,
        ),
        (
            r"/api/v2/servers/([a-z0-9-]+)/webhook/?",
            ApiServersServerWebhooksIndexHandler,
            handler_args,
        ),
        (
            # optional third argument when we need a action ID
            r"/api/v2/servers/([a-z0-9-]+)/action/([a-z_]+)(?:/([a-z0-9-]+))?/?",
            ApiServersServerActionHandler,
            handler_args,
        ),
        (
            r"/api/v2/servers/([a-z0-9-]+)/logs/?",
            ApiServersServerLogsHandler,
            handler_args,
        ),
        (
            r"/api/v2/servers/([a-z0-9-]+)/logs/files/?",
            ApiServersServerLogFilesHandler,
            handler_args,
        ),
        (
            r"/api/v2/servers/([a-z0-9-]+)/users/?",
            ApiServersServerUsersHandler,
            handler_args,
        ),
        (
            r"/api/v2/servers/([a-z0-9-]+)/public/?",
            ApiServersServerPublicHandler,
            handler_args,
        ),
        (
            r"/api/v2/servers/([a-z0-9-]+)/stdin/?",
            ApiServersServerStdinHandler,
            handler_args,
        ),
        (
            r"/api/v2/roles/?",
            ApiRolesIndexHandler,
            handler_args,
        ),
        (
            r"/api/v2/roles/([0-9]+)/?",
            ApiRolesRoleIndexHandler,
            handler_args,
        ),
        (
            r"/api/v2/roles/([0-9]+)/servers/?",
            ApiRolesRoleServersHandler,
            handler_args,
        ),
        (
            r"/api/v2/roles/([0-9]+)/users/?",
            ApiRolesRoleUsersHandler,
            handler_args,
        ),
        (
            r"/api/v2/jsonschema/?",
            ApiJsonSchemaListHandler,
            handler_args,
        ),
        (
            r"/api/v2/jsonschema/([a-z0-9_]+)/?",
            ApiJsonSchemaHandler,
            handler_args,
        ),
        (
            r"/api/v2/?",
            ApiIndexHandler,
            handler_args,
        ),
        (
            r"/api/v2/(.*)",
            ApiNotFoundHandler,
            handler_args,
        ),
    ]
