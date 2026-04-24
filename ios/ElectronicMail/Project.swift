import ProjectDescription

let project = Project(
    name: "ElectronicMail",
    targets: [
        .target(
            name: "ElectronicMailCore",
            destinations: .iOS,
            product: .framework,
            bundleId: "app.electronicmail.core",
            deploymentTargets: .iOS("17.0"),
            infoPlist: .default,
            sources: ["ElectronicMail/Core/**"],
            dependencies: []
        ),
        .target(
            name: "ElectronicMail",
            destinations: .iOS,
            product: .app,
            bundleId: "app.electronicmail.ios",
            deploymentTargets: .iOS("17.0"),
            infoPlist: .extendingDefault(with: [
                "CFBundleDisplayName": "ElectronicMail",
                "UILaunchScreen": [
                    "UIColorName": ""
                ],
                "CFBundleURLTypes": [
                    [
                        "CFBundleURLName": "app.electronicmail.ios",
                        "CFBundleURLSchemes": ["electronicmail"]
                    ]
                ],
                "NSAppTransportSecurity": [
                    "NSAllowsArbitraryLoads": true
                ]
            ]),
            sources: ["ElectronicMail/App/**"],
            resources: ["ElectronicMail/Resources/**"],
            dependencies: [
                .target(name: "ElectronicMailCore")
            ]
        ),
        .target(
            name: "ElectronicMailTests",
            destinations: .iOS,
            product: .unitTests,
            bundleId: "app.electronicmail.ios.tests",
            deploymentTargets: .iOS("17.0"),
            infoPlist: .default,
            sources: ["ElectronicMail/Tests/**"],
            dependencies: [
                .target(name: "ElectronicMailCore")
            ]
        )
    ]
)
