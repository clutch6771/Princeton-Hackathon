
# Project Instructions

## Overview
This project combines:
- Python for machine learning and AI agents
- Swift / SwiftUI for the iOS application
- Xcode for iOS builds and development

## Architecture
- Python components implement machine learning
  and agent functionality.
- Swift components implement the iOS application.
- Maintain clear separation between ML logic
  and iOS user interface code.
- Follow the project's existing integration
  architecture.
- Do not assume how Python communicates with
  the iOS app; inspect the implementation first.

## Python Guidelines
- Follow the ECC Python and common rules.
- Use type hints where appropriate.
- Prefer modular, testable agent components.
- Write tests for agent behavior and data
  processing.
- Follow the existing dependency management
  system.
- Avoid hardcoding credentials or API keys.

## Swift / iOS Guidelines
- Follow the ECC Swift and common rules.
- Use the project's established SwiftUI/UIKit
  architecture.
- Keep UI and business logic separated.
- Respect Swift concurrency and actor isolation.
- Write unit tests for application logic.
- Validate changes with the Xcode build system.

## Machine Learning Guidelines
- Document model inputs and outputs.
- Validate input and output data.
- Test agent behavior using reproducible cases.
- Keep secrets, sensitive datasets and private
  training data out of Git.
- Follow the team's model versioning strategy.

## Development Workflow
- Make changes on the assigned feature branch.
- Review existing code before modifying it.
- Plan changes that affect both Python and iOS.
- Prefer small, reviewable changes.
- Run relevant tests before committing.
- Never commit secrets or local credentials.
- Use pull requests to merge into main.
