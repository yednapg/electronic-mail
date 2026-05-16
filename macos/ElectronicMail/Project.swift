import ProjectDescription

let project = Project(
    name: "ElectronicMail",
    targets: [
        .target(
            name: "ElectronicMailCore",
            destinations: .macOS,
            product: .framework,
            bundleId: "com.rameshpandey.ElectronicMailCore",
            deploymentTargets: .macOS("14.0"),
            infoPlist: .default,
            sources: ["ElectronicMail/Core/**"],
            dependencies: []
        ),
        .target(
            name: "ElectronicMail",
            destinations: .macOS,
            product: .app,
            bundleId: "com.rameshpandey.ElectronicMail",
            deploymentTargets: .macOS("14.0"),
            infoPlist: .extendingDefault(with: [
                "CFBundleDisplayName": "Electronic Mail",
                "CFBundleName": "Electronic Mail",
                "LSMinimumSystemVersion": "14.0",
                "CFBundleURLTypes": [
                    [
                        "CFBundleURLName": "com.rameshpandey.ElectronicMail",
                        "CFBundleURLSchemes": ["electronicmail"]
                    ]
                ],
                "NSAppTransportSecurity": [
                    "NSAllowsArbitraryLoads": true
                ]
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
            bundleId: "com.rameshpandey.ElectronicMailTests",
            deploymentTargets: .macOS("14.0"),
            infoPlist: .default,
            sources: ["ElectronicMail/Tests/**"],
            dependencies: [
                .target(name: "ElectronicMailCore")
            ]
        )
    ]
)
