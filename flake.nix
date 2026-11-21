{
  description = "hass-mestro — Home Assistant integration for the mestro player";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs =
    { nixpkgs, ... }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      # nixpkgs has no pytest-homeassistant-custom-component, so the test
      # deps live in a uv venv (see README, "Development"); the shell only
      # provides the interpreter, uv and ruff.
      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = with pkgs; [
            python314
            uv
            ruff
          ];
          # manylinux wheels (numpy, cryptography, …) dlopen these
          LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [
            pkgs.stdenv.cc.cc.lib
            pkgs.zlib
          ];
          UV_PYTHON_DOWNLOADS = "never";
          UV_PYTHON = "${pkgs.python314}/bin/python3.14";
          shellHook = ''
            export HASS_VENV="''${HASS_VENV:-$PWD/.venv}"
            if [ -x "$HASS_VENV/bin/python" ]; then
              export VIRTUAL_ENV="$HASS_VENV"
              export PATH="$HASS_VENV/bin:$PATH"
            fi
          '';
        };
      });
    };
}
