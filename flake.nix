{
  description = "A very basic flake";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    poetry2nix = {
      url = "github:nix-community/poetry2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    nr-utils = {
      url = "github:super-secret-github-user/python-nr.util";
      flake = false;
    };
  };

  outputs = { self, nixpkgs, poetry2nix, nr-utils, flake-utils}:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        inherit (poetry2nix.legacyPackages.${system}) mkPoetryApplication defaultPoetryOverrides;
      in
      rec {
        packages = flake-utils.lib.flattenTree {
          builddsl = (mkPoetryApplication {
            projectDir = ./.;
          });
        };

        devShells.default = pkgs.mkShell {
          name = "Helsing tooling";

          buildInputs = [
	        packages.builddsl
          ];
        };
      }
    );
}