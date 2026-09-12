import ProjectDescription

let portableCoreSources: SourceFilesList = [
    "ElectronicMail/Core/AppClient.swift",
    "ElectronicMail/Core/AppConfiguration.swift",
    "ElectronicMail/Core/AppSessionCache.swift",
    "ElectronicMail/Core/AppSessionMapping.swift",
    "ElectronicMail/Core/AttachmentPrefetchCoordinator.swift",
    "ElectronicMail/Core/DemoAppClient.swift",
    "ElectronicMail/Core/EncryptedMediaCache.swift",
    "ElectronicMail/Core/InboxStore.swift",
    "ElectronicMail/Core/LocalMailStore.swift",
    "ElectronicMail/Core/OfflineFirstAppClient.swift",
    "ElectronicMail/Core/OfflineContentSyncCoordinator.swift",
    "ElectronicMail/Core/RemoteImageLoader.swift",
    "ElectronicMail/Core/SQLiteLocalMailStore.swift",
    "ElectronicMail/Core/Models.swift",
    "ElectronicMail/Core/MailNotifications.swift",
    "ElectronicMail/Core/SessionTokenStore.swift",
    "ElectronicMail/Core/ThreadCache.swift",
    "ElectronicMail/Shared/**",
]

let project = Project(
    name: "ElectronicMail",
    targets: [
        .target(
            name: "ElectronicMailShared",
            destinations: [.iPhone, .mac],
            product: .framework,
            bundleId: "app.electronicmail.shared",
            deploymentTargets: .multiplatform(iOS: "17.0", macOS: "14.0"),
            infoPlist: .file(path: "Config/InfoPlists/ElectronicMailShared-Info.plist"),
            sources: portableCoreSources,
            dependencies: [
                .sdk(name: "sqlite3", type: .library)
            ]
        ),
        .target(
            name: "ElectronicMailCore",
            destinations: .macOS,
            product: .framework,
            bundleId: "app.electronicmail.core",
            deploymentTargets: .macOS("14.0"),
            infoPlist: .file(path: "Config/InfoPlists/ElectronicMailCore-Info.plist"),
            sources: [
                "ElectronicMail/Core/**",
                "ElectronicMail/Shared/AIInboxDomain.swift",
                "ElectronicMail/Shared/GmailAccountSettings.swift",
                "ElectronicMail/Shared/MobileAuthFlow.swift",
            ],
            resources: [
                "ElectronicMail/Mac/PrivacyInfo.xcprivacy"
            ],
            dependencies: [
                .sdk(name: "sqlite3", type: .library)
            ],
            settings: .settings(base: [
                "ENABLE_HARDENED_RUNTIME": "YES",
                "SWIFT_ACTIVE_COMPILATION_CONDITIONS": "$(inherited) ELECTRONIC_MAIL_SHARED_AI_DOMAIN"
            ])
        ),
        .target(
            name: "ElectronicMail",
            destinations: .macOS,
            product: .app,
            bundleId: "app.electronicmail.mac",
            deploymentTargets: .macOS("14.0"),
            infoPlist: .file(path: "Config/InfoPlists/ElectronicMail-Info.plist"),
            entitlements: .file(path: "ElectronicMail/Mac/ElectronicMail.entitlements"),
            sources: ["ElectronicMail/Mac/**"],
            resources: [
                "ElectronicMail/Mac/Assets.xcassets",
                "ElectronicMail/Mac/PrivacyInfo.xcprivacy"
            ],
            dependencies: [
                .target(name: "ElectronicMailCore")
            ],
            settings: .settings(
                base: [
                    "CURRENT_PROJECT_VERSION": "1",
                    "ELECTRONIC_MAIL_SOURCE_COMMIT": "local",
                    "ENABLE_HARDENED_RUNTIME": "YES",
                    "EXECUTABLE_NAME": "Electronic Mail",
                    "MARKETING_VERSION": "1.0.0",
                    "PRODUCT_MODULE_NAME": "ElectronicMail",
                    "PRODUCT_NAME": "Electronic Mail"
                ],
                configurations: [
                    .debug(name: "Debug", settings: [
                        "ELECTRONIC_MAIL_APNS_ENVIRONMENT": "development",
                        "ELECTRONIC_MAIL_BACKEND_URL": "http://localhost:3001"
                    ]),
                    .release(name: "Release", settings: [
                        "ELECTRONIC_MAIL_APNS_ENVIRONMENT": "production",
                        "ELECTRONIC_MAIL_BACKEND_URL": "https://electronic-mail-backend.invalid",
                        "ENABLE_PREVIEWS": "NO",
                        "INFOPLIST_FILE": "Config/InfoPlists/ElectronicMail-Release-Info.plist",
                        "ONLY_ACTIVE_ARCH": "NO"
                    ])
                ]
            )
        ),
        .target(
            name: "ElectronicMailTests",
            destinations: .macOS,
            product: .unitTests,
            bundleId: "app.electronicmail.tests",
            deploymentTargets: .macOS("14.0"),
            infoPlist: .file(path: "Config/InfoPlists/ElectronicMailTests-Info.plist"),
            sources: ["ElectronicMail/Tests/**"],
            dependencies: [
                .target(name: "ElectronicMailCore")
            ]
        ),
        .target(
            name: "ElectronicMailiOS",
            destinations: [.iPhone],
            product: .app,
            bundleId: "app.electronicmail.ios",
            deploymentTargets: .iOS("17.0"),
            infoPlist: .file(path: "Config/InfoPlists/ElectronicMailiOS-Info.plist"),
            entitlements: .file(path: "ElectronicMail/iOS/ElectronicMailiOS.entitlements"),
            sources: ["ElectronicMail/iOS/**"],
            resources: ["ElectronicMail/iOS/Resources/**"],
            dependencies: [
                .target(name: "ElectronicMailShared")
            ],
            settings: .settings(
                base: [
                    "CODE_SIGN_STYLE": "Automatic",
                    "CURRENT_PROJECT_VERSION": "1",
                    "DEVELOPMENT_TEAM": "B8Y93JD4VQ",
                    "MARKETING_VERSION": "1.0.0",
                    "TARGETED_DEVICE_FAMILY": "1"
                ],
                configurations: [
                    .debug(name: "Debug", settings: [
                        "ELECTRONIC_MAIL_APNS_ENVIRONMENT": "development",
                        "ELECTRONIC_MAIL_IOS_BACKEND_URL": "http://localhost:3001"
                    ]),
                    .release(name: "Release", settings: [
                        "ELECTRONIC_MAIL_APNS_ENVIRONMENT": "production",
                        "ELECTRONIC_MAIL_IOS_BACKEND_URL": "https://electronic-mail-backend.invalid",
                        "ENABLE_PREVIEWS": "NO"
                    ])
                ]
            )
        ),
        .target(
            name: "ElectronicMailiOSTests",
            destinations: [.iPhone],
            product: .unitTests,
            bundleId: "app.electronicmail.ios.tests",
            deploymentTargets: .iOS("17.0"),
            infoPlist: .default,
            sources: ["ElectronicMail/iOSTests/**"],
            dependencies: [
                .target(name: "ElectronicMailiOS")
            ]
        ),
        .target(
            name: "ElectronicMailiOSUITests",
            destinations: [.iPhone],
            product: .uiTests,
            bundleId: "app.electronicmail.ios.ui-tests",
            deploymentTargets: .iOS("17.0"),
            infoPlist: .default,
            sources: ["ElectronicMail/iOSUITests/**"],
            dependencies: [
                .target(name: "ElectronicMailiOS")
            ]
        ),
        .target(
            name: "ElectronicMailSharedTests",
            destinations: .macOS,
            product: .unitTests,
            bundleId: "app.electronicmail.shared.tests",
            deploymentTargets: .macOS("14.0"),
            infoPlist: .file(path: "Config/InfoPlists/ElectronicMailSharedTests-Info.plist"),
            sources: ["ElectronicMail/SharedTests/**"],
            dependencies: [
                .target(name: "ElectronicMailShared")
            ]
        )
    ]
)
