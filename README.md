# hal

A runtime environment for [Haskell] applications running on [AWS Lambda].

#### Flexible

This library uniquely supports different types of AWS Lambda Handlers for your needs/comfort with advanced Haskell.
Instead of exposing a single function that constructs a Lambda, this library exposes many.

For lambdas that are pure and safe, then `pureRuntime` is ideal.
It accepts a handler with the signature `(FromJSON a, ToJSON b) => a -> b`.
This runtime guarantees that side-effects cannot occur.

For advanced use cases `mRuntime` unlocks the full power of Monad Transformers.
It accepts handlers with the signature `(MonadCatch m, MonadIO m, FromJSON event, ToJSON result) =>  (event -> m result)`
This enables users to add caching logic or expose complex environments.

With numerous options in between these two, developers can choose the right balance of flexibility vs simplicity.

#### Performant

Measuring lambda performance is tricky, so investigation and optimization is ongoing.
Current indications show a _warm_ execution overhead of only ~20% more than the official [Rust Runtime] (a much lower level language).

#### Robust

While testing continues, we have executed over 30k test events without error caused by the runtime.
Naive approaches lead to error rates well over 10%.

## Table of Contents

  - [Supported Platforms / GHC Versions](#supported-platforms-ghc-versions)
  - [Quick Start](#quick-start)
  - [Local Testing](#local-testing)

## Supported Platforms / GHC Versions

This fork targets GHC 9.10.3, the committed Cabal freeze, and Stack LTS 24.66.
Aeson must be at least 2.2.5.1 and Scientific at least 0.3.10.0. Older compiler
and resolver configurations are retired as a compatibility change. Nix is no
longer advertised or covered by a build workflow.

See [the support contract](docs/support.md) for tested platforms, exact commands,
dependency advisories, and validation limits. These are fork-specific decisions;
they do not describe Nike upstream's support policy or prove AWS deployment
compatibility.

## Quick Start

The deployment examples below are historical usage guidance. They have not been
validated against a live AWS account in this fork's dependency update. Use the
fixed resolver `lts-24.66` and verify the OS/architecture and libc compatibility
of your deployment image separately.

This quick start assumes you have the following tools installed:

  - [Stack][stack.yaml]
  - [Docker]
  - [aws-cli]

Add `hal` to your [stack.yaml]'s [`extra-deps`] and enable
[Docker] integration so that your binary is automatically compiled in a
compatible environment for AWS. Also add `hal` to your project's
dependency list (either `project-name.cabal` or `package.yaml`)

```yaml
#...
extra-deps:
  - hal-${DESIRED_VERSION}
# ...
docker:
  enable: true
# ...
```

Then, define your types and handler:

```haskell
{-# LANGUAGE DeriveGeneric  #-}
{-# LANGUAGE NamedFieldPuns #-}

module Main where

import AWS.Lambda.Runtime (pureRuntime)
import Data.Aeson         (FromJSON, ToJSON)
import GHC.Generics       (Generic)

data IdEvent  = IdEvent { input   :: String } deriving Generic
instance FromJSON IdEvent

data IdResult = IdResult { output :: String } deriving Generic
instance ToJSON IdResult

handler :: IdEvent -> IdResult
handler IdEvent { input } = IdResult { output = input }

main :: IO ()
main = pureRuntime handler
```

Your binary should be called `bootstrap` in order for the custom runtime
to execute properly:

```yaml
# Example snippet of package.yaml
# ...
executables:
  bootstrap:
    source-dirs: src
    main: Main.hs  # e.g. {project root}/src/Main.hs
# ...
```

You'll need to either build on a compatible linux host or inside a compatible docker container.
Note that current Stack LTS images are _not_ compatible.
If you see an error message that contains "version 'GLIBC_X.XX' not found" when running (hosted or locally), then your build environment is not compatible.

Enable stack's docker integration and define an optional image within stack.yaml:

```yaml
# file: stack.yaml
docker:
  enable: true
  # If omitted, this defaults to fpco/stack-build:lts-${YOUR_LTS_VERSION}
  image: ${BUILD_IMAGE}
```

Don't forget to define your [CloudFormation] stack:

```yaml
# file: template.yaml
AWSTemplateFormatVersion: '2010-09-09'
Transform: 'AWS::Serverless-2016-10-31'
Description: Test for the Haskell Runtime.
Resources:
  HelloWorldApp:
    Type: 'AWS::Serverless::Function'
    Properties:
      Handler: NOT_USED
      Runtime: provided
      # CodeUri is a relative path from the directory that this CloudFormation
      # file is defined.
      CodeUri: .stack-work/docker/_home/.local/bin/
      Description: My Haskell runtime.
      MemorySize: 128
      Timeout: 3
```

Finally, build, upload and test your lambda!

```bash
# Build the binary, make sure your executable is named `bootstrap`
stack build --copy-bins

# Create your function package
aws cloudformation package \
  --template-file template.yaml \
  --s3-bucket your-existing-bucket > \
  deployment_stack.yaml

# Deploy your function
aws cloudformation deploy \
  --stack-name "hello-world-haskell" \
  --region us-west-2 \
  --capabilities CAPABILITY_IAM \
  --template-file deployment_stack.yaml

# Take it for a spin!
aws lambda invoke \
  --function-name your-function-name \
  --region us-west-2 \
  --payload '{"input": "foo"}' \
  output.txt
```

## Local Testing

### Dependencies

  - [Stack][stack.yaml]
  - [Docker]

### Build

```bash
stack --no-docker test
stack --no-docker --stack-yaml examples/stack.yaml build
```

### Execute w/ Docker

This historical example needs a separately validated Lambda-compatible build image.

```bash
echo '{ "accountId": "byebye" }' | docker run -i --rm \
    -e DOCKER_LAMBDA_USE_STDIN=1 \
    -v ${PWD}/.stack-work/docker/_home/.local/bin/:/var/task \
    lambci/lambda:provided
```

### Execute w/ SAM Local

This historical [aws-sam-cli] example predates version 1.0 and has not been revalidated.

```bash
echo '{ "accountId": "byebye" }' | sam local invoke --region us-east-1
```

[AWS Lambda]: https://docs.aws.amazon.com/lambda/latest/dg/welcome.html
[Haskell]: https://www.haskell.org/
[stack.yaml]: https://docs.haskellstack.org/
[`extra-deps`]: https://docs.haskellstack.org/en/stable/yaml_configuration/#yaml-configuration
[Docker]: https://www.docker.com/why-docker
[aws-cli]: https://aws.amazon.com/cli/
[CloudFormation]: https://aws.amazon.com/cloudformation/
[aws-sam-cli]: https://github.com/awslabs/aws-sam-cli
[Rust Runtime]: https://github.com/awslabs/aws-lambda-rust-runtime
[lambda-env]: https://docs.aws.amazon.com/lambda/latest/dg/current-supported-versions.html
[ci]: https://github.com/Nike-Inc/hal/actions
[stackage]: https://www.stackage.org/
[GHC]: https://www.haskell.org/ghc/download.html
[Cabal]: https://www.haskell.org/cabal/download.html
