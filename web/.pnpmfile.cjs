// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

// Allow esbuild to run its postinstall script
module.exports = {
  hooks: {
    readPackage(pkg) {
      return pkg;
    }
  }
};
