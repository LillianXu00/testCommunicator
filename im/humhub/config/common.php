<?php
return [
    'components' => [
        'db' => [
            'class' => 'yii\\db\\Connection',
            'dsn' => sprintf('mysql:host=%s;dbname=%s', getenv('DB_HOST') ?: 'db', getenv('HUMHUB_DB_NAME') ?: 'humhub'),
            'username' => getenv('HUMHUB_DB_USER') ?: 'humhub',
            'password' => getenv('HUMHUB_DB_PASSWORD') ?: '',
            'charset' => 'utf8mb4',
            'tablePrefix' => '',
        ],
        'cache' => [
            'class' => 'yii\\redis\\Cache',
            'redis' => [
                'hostname' => getenv('REDIS_HOST') ?: 'redis',
                'port' => (int) (getenv('REDIS_PORT') ?: 6379),
                'password' => getenv('REDIS_PASSWORD') ?: null,
                'database' => 0,
            ],
        ],
        'queue' => [
            'class' => 'humhub\\modules\\queue\\driver\\Redis',
            'redis' => [
                'hostname' => getenv('REDIS_HOST') ?: 'redis',
                'port' => (int) (getenv('REDIS_PORT') ?: 6379),
                'password' => getenv('REDIS_PASSWORD') ?: null,
                'database' => 1,
            ],
            'channel' => 'humhub-queue',
        ],
        'settings' => [
            'class' => 'humhub\\components\\SettingsManager',
        ],
        'user' => [
            'enableAutoLogin' => false,
        ],
        'urlManager' => [
            'showScriptName' => false,
        ],
    ],
    'params' => [
        'enableRegistration' => false,
        'enableUserApproval' => true,
        'mailerTransportType' => 'php',
        'baseUrl' => getenv('PUBLIC_BASE_URL') ?: 'http://localhost',
        'installed' => false,
    ],
    'modules' => [
        'jwt-sso' => [
            'class' => 'humhub\\modules\\jwt_sso\\Module',
            'sharedSecret' => getenv('JWT_SSO_SHARED_SECRET') ?: '',
            'issuer' => getenv('JWT_SSO_ISSUER') ?: 'im-auth-broker',
            'audience' => getenv('JWT_SSO_AUDIENCE') ?: 'humhub',
            'autoCreateUser' => false,
        ],
    ],
];
