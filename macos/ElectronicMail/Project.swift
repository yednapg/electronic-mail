import ProjectDescription

let portableCoreSources: SourceFilesList = [
    "ElectronicMail/Core/AppClient.swift",
    "ElectronicMail/Core/AppConfiguration.swift",
    "ElectronicMail/Core/AppSessionCache.swift",
    "ElectronicMail/Core/AppSessionMapping.swift",
    "ElectronicMail/Core/DemoAppClient.swift",
    "ElectronicMail/Core/InboxStore.swift",
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
            infoPlist: .default,
            sources: portableCoreSources,
            dependencies: []
        ),
        .target(
            name: "ElectronicMailCore",
            destinations: .macOS,
            product: .framework,
            bundleId: "app.electronicmail.core",
            deploymentTargets: .macOS("14.0"),
            infoPlist: .default,
            sources: ["ElectronicMail/Core/**"],
            dependencies: []
        ),
        .target(
            name: "ElectronicMail",
            destinations: .macOS,
            product: .app,
            bundleId: "app.electronicmail.mac",
            deploymentTargets: .macOS("14.0"),
            infoPlist: .extendingDefault(with: [
                "CFBundleDisplayName": "Electronic Mail",
                "CFBundleName": "Electronic Mail",
                "LSMinimumSystemVersion": "14.0",
                "CFBundleURLTypes": [
                    [
                        "CFBundleURLName": "app.electronicmail.mac",
                        "CFBundleURLSchemes": ["electronicmail"]
                    ]
                ],
                "NSAppTransportSecurity": [
                    "NSAllowsArbitraryLoads": true
                ],
                "NSRequiresAquaSystemAppearance": false
            ]),
            sources: ["ElectronicMail/Mac/**"],
            dependencies: [
                .target(name: "ElectronicMailCore")
            ]
        ),
        .target(
            name: "ElectronicMailTests",
            destinations: .macOS,
            product: .unitTests,
            bundleId: "app.electronicmail.tests",
            deploymentTargets: .macOS("14.0"),
            infoPlist: .default,
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
            infoPlist: .extendingDefault(with: [
                "CFBundleDisplayName": "Electronic Mail",
                "CFBundleName": "Electronic Mail",
                "CFBundleURLTypes": [
                    [
                        "CFBundleURLName": "app.electronicmail.ios",
                        "CFBundleURLSchemes": ["electronicmail"]
                    ]
                ],
                "BackendBaseURL": "$(ELECTRONIC_MAIL_IOS_BACKEND_URL)",
                "UILaunchScreen": [:],
                "UISupportedInterfaceOrientations": [
                    "UIInterfaceOrientationPortrait"
                ]
            ]),
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
            infoPlist: .default,
            sources: ["ElectronicMail/SharedTests/**"],
            dependencies: [
                .target(name: "ElectronicMailShared")
            ]
        )
    ]
)
