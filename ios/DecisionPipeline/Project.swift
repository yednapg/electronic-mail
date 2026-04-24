import ProjectDescription

let project = Project(
    name: "DecisionPipeline",
    targets: [
        .target(
            name: "DecisionPipelineCore",
            destinations: .iOS,
            product: .framework,
            bundleId: "com.rameshpandey.DecisionPipelineCore",
            deploymentTargets: .iOS("17.0"),
            infoPlist: .default,
            sources: ["DecisionPipeline/Core/**"],
            dependencies: []
        ),
        .target(
            name: "DecisionPipeline",
            destinations: .iOS,
            product: .app,
            bundleId: "com.rameshpandey.DecisionPipeline",
            deploymentTargets: .iOS("17.0"),
            infoPlist: .extendingDefault(with: [
                "CFBundleDisplayName": "Decision Pipeline",
                "UILaunchScreen": [
                    "UIColorName": ""
                ],
                "CFBundleURLTypes": [
                    [
                        "CFBundleURLName": "com.rameshpandey.DecisionPipeline",
                        "CFBundleURLSchemes": ["decisionpipeline"]
                    ]
                ],
                "NSAppTransportSecurity": [
                    "NSAllowsArbitraryLoads": true
                ]
            ]),
            sources: ["DecisionPipeline/App/**"],
            resources: ["DecisionPipeline/Resources/**"],
            dependencies: [
                .target(name: "DecisionPipelineCore")
            ]
        ),
        .target(
            name: "DecisionPipelineTests",
            destinations: .iOS,
            product: .unitTests,
            bundleId: "com.rameshpandey.DecisionPipelineTests",
            deploymentTargets: .iOS("17.0"),
            infoPlist: .default,
            sources: ["DecisionPipeline/Tests/**"],
            dependencies: [
                .target(name: "DecisionPipelineCore")
            ]
        )
    ]
)
