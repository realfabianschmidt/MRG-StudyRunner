# Affect Map card

Affect Map is a separate card type (`affect-map`) based on the positional Field
and Orbit views of Mood Meter. The original `mood-meter` card and its four
views remain available for existing studies. Field records selected words,
`pleasantness`, and `energy` (each 0–1); Orbit additionally records
`intensity` (0–1). The card is answered only after both a position and at
least one word have been selected.

The editor offers four region colors (`region_colors.red`, `.yellow`, `.green`,
`.blue`) and `colors_enabled`. Colors default to Mood Meter's palette. Turning
colors off makes regions, light, words, and the editor preview neutral gray;
the saved colors are retained and return when the switch is turned on. These
settings affect presentation only and are never included in an answer.

The two views and their core are copied into this plugin so it can be removed
independently of Mood Meter. Per-session state is keyed by `affect-map`, not
`mood-meter`, preventing one card from carrying an answer into the other.

Scientific background and word placement are described in the Mood Meter
README. Field follows the Affect Grid; Orbit follows the circumplex model
and the Geneva Emotion Wheel's reading of angle and intensity.
