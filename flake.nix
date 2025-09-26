{
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
  };

  outputs = { self , nixpkgs ,... }: let
    system = "x86_64-linux";
  in {
    devShells."${system}".default = let
      pkgs = import nixpkgs {
        inherit system;
      };
    in pkgs.mkShellNoCC {
      packages = with pkgs; [
        python311
      ];

      shellHook = ''
        if [[ -f .venv/bin/activate ]]; then
          source .venv/bin/activate
        else
          python -m venv .venv
          source .venv/bin/activate
          pip install -e .
        fi
      '';
    };
  };
}
