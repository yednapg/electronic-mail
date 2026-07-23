import ProjectDescription

let portableCoreSources: SourceFilesList = [
    "ElectronicMail/Core/AppClient.swift",
    "ElectronicMail/Core/AppConfiguration.swift",
    "ElectronicMail/Core/AppSessionCache.swift",
    "ElectronicMail/Core/AppSessionMapping.swift",
    "ElectronicMail/Core/DemoAppClient.swift",
    "ElectronicMail/Core/InboxStore.swift",
    "ElectronicMail/Core/LocalMailStore.swift",
    "ElectronicMail/Core/Models.swift",
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
            dependencies: []
        ),
        .target(
            name: "ElectronicMailCore",
            destinations: .macOS,
            product: .framework,
            bundleId: "app.electronicmail.core",
            deploymentTargets: .macOS("14.0"),
            infoPlist: .file(path: "Config/InfoPlists/ElectronicMailCore-Info.plist"),
            sources: ["ElectronicMail/Core/**"],
            resources: [
                "ElectronicMail/Mac/PrivacyInfo.xcprivacy"
            ],
            dependencies: [
                .sdk(name: "sqlite3", type: .library)
            ],
            settings: .settings(base: [
                "ENABLE_HARDENED_RUNTIME": "YES"
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
                    "MARKETING_VERSION": "1.0.0"
                ],
                configurations: [
                    .debug(name: "Debug", settings: [
                        "ELECTRONIC_MAIL_BACKEND_URL": "http://localhost:3001"
                    ]),
                    .release(name: "Release", settings: [
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
            sources: ["ElectronicMail/iOS/**"],
            dependencies: [
                .target(name: "ElectronicMailShared")
            ],
            settings: .settings(base: [
                "CODE_SIGN_STYLE": "Automatic",
                "ELECTRONIC_MAIL_IOS_BACKEND_URL": "https://electronic-mail-backend.invalid",
                "TARGETED_DEVICE_FAMILY": "1"
            ])
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
