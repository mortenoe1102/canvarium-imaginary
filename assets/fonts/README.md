# Fonts

Optional vendored UI fonts for local preview rendering.

Resolution order prefers:

1. files placed in this folder
2. user-local font dirs such as `~/.local/share/fonts` and `~/.fonts`
3. system fontconfig matches

Preferred family order:

1. `Helvetica Now Text`
2. `Helvetica Now Display`
3. `Helvetica Neue`
4. `Helvetica`
5. `Inter`
6. `Arial`
7. `Nimbus Sans`
8. `Liberation Sans`
9. `DejaVu Sans`
10. `Noto Sans`

If you later provide a licensed Helvetica-family font here, the local preview UI will use it first.
