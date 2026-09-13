import React from 'react';
import { useTranslation } from 'react-i18next';
import { StyleSheet, Text, View, useWindowDimensions } from 'react-native';
import Svg, { Circle, Path } from 'react-native-svg';

import { theme } from '../theme';
import { Disc } from './Disc';
import { Icon } from './icon/Icon';

/** The graph is laid out on a fixed canvas and scaled to fit, so its lines always meet its nodes. */
const CANVAS = { width: 330, height: 334 };
const NODE_A = { left: 40, top: 0, width: 250, height: 60 };
const NODE_B = { top: 110, width: 146, height: 108 };
const NODE_C = { left: 35, top: 270, width: 260, height: 64 };

const COOL = ['M165 60 C165 88 73 82 73 110', 'M165 60 C165 88 257 82 257 110'];
const HOT = [
  'M73 218 C73 250 165 238 165 270',
  'M257 218 C257 250 165 238 165 270',
];
const COOL_PORTS = [
  [165, 60],
  [73, 110],
  [257, 110],
] as const;
const HOT_PORTS = [
  [73, 218],
  [257, 218],
  [165, 270],
] as const;

/**
 * When the user's phone rings, drawn as the rule the code follows.
 *
 * The assistant answers; if it cannot resolve the call, or the call is urgent, the phone rings;
 * anything else waits in Activity. The lines end exactly at the node edges, and the two that
 * lead to the user are apricot, the colour that only ever means the user is needed.
 */
export function CallGraph(): React.JSX.Element {
  const { t } = useTranslation();
  const { width } = useWindowDimensions();
  const available = width - theme.space.gutter * 2;
  const scale = Math.min(1, available / CANVAS.width);

  return (
    <View
      accessible
      accessibilityLabel={t('calls.graph.described')}
      style={styles.frame}
      testID="call-graph"
    >
      {/* A box of the scaled size holds the canvas, which is scaled about its own centre — so the
          translation puts its top-left corner back where the box starts. */}
      <View
        style={{ width: CANVAS.width * scale, height: CANVAS.height * scale }}
      >
        <View
          style={[
            styles.canvas,
            {
              transform: [
                { translateX: (-(1 - scale) * CANVAS.width) / 2 },
                { translateY: (-(1 - scale) * CANVAS.height) / 2 },
                { scale },
              ],
            },
          ]}
        >
          <Svg
            width={CANVAS.width}
            height={CANVAS.height}
            style={StyleSheet.absoluteFill}
          >
            {COOL.map(d => (
              <Path
                key={d}
                d={d}
                stroke={theme.colour.border}
                strokeWidth={2.5}
                fill="none"
                strokeLinecap="round"
              />
            ))}
            {HOT.map(d => (
              <Path
                key={d}
                d={d}
                stroke={theme.colour.needsYou}
                strokeWidth={2.5}
                fill="none"
                strokeLinecap="round"
              />
            ))}
            {COOL_PORTS.map(([x, y]) => (
              <Circle
                key={`${x}-${y}`}
                cx={x}
                cy={y}
                r={4.5}
                fill={theme.colour.surface}
                stroke={theme.colour.border}
                strokeWidth={2.5}
              />
            ))}
            {HOT_PORTS.map(([x, y]) => (
              <Circle
                key={`${x}-${y}`}
                cx={x}
                cy={y}
                r={4.5}
                fill={theme.colour.surface}
                stroke={theme.colour.needsYou}
                strokeWidth={2.5}
              />
            ))}
          </Svg>

          <View style={[styles.node, styles.pill, NODE_A]}>
            <View style={styles.botDisc}>
              <Icon name="bot" colour={theme.colour.onAccent} size={20} />
            </View>
            <Text style={styles.pillText}>{t('calls.graph.answers')}</Text>
          </View>

          <View style={[styles.node, styles.card, NODE_B, styles.leftNode]}>
            <Disc icon="bubble-q" tone="assistant" size={44} />
            <Text style={styles.cardText}>{t('calls.graph.cantResolve')}</Text>
          </View>
          <View style={[styles.node, styles.card, NODE_B, styles.rightNode]}>
            <Disc icon="siren" tone="needsYou" size={44} />
            <Text style={styles.cardText}>{t('calls.graph.urgent')}</Text>
          </View>

          <View style={[styles.node, styles.pill, styles.rings, NODE_C]}>
            <View style={styles.ringDisc}>
              <Icon name="phone-ring" colour="#8A4A0E" size={20} />
            </View>
            <Text style={styles.ringText}>{t('calls.graph.rings')}</Text>
          </View>
        </View>
      </View>

      <View style={styles.else}>
        <Disc icon="inbox" size={32} />
        <Text style={styles.elseText}>{t('calls.graph.otherwise')}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  frame: { alignItems: 'center' },
  canvas: { width: CANVAS.width, height: CANVAS.height },
  leftNode: { left: 0 },
  rightNode: { left: CANVAS.width - NODE_B.width },
  node: {
    position: 'absolute',
    backgroundColor: theme.colour.surface,
    ...theme.shadow.card,
  },
  pill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: theme.space.row,
    borderRadius: theme.radius.pill,
    paddingLeft: theme.space.sm,
    paddingRight: theme.space.md,
  },
  botDisc: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: theme.colour.accent,
    alignItems: 'center',
    justifyContent: 'center',
  },
  pillText: { ...theme.type.strong, color: theme.colour.text },
  card: {
    borderRadius: theme.radius.md,
    alignItems: 'center',
    justifyContent: 'center',
    gap: theme.space.sm,
    paddingHorizontal: theme.space.sm,
  },
  cardText: {
    ...theme.type.subtitle,
    fontFamily: theme.font.strong,
    color: theme.colour.text,
    textAlign: 'center',
  },
  rings: { backgroundColor: theme.colour.needsYou, ...theme.shadow.raised },
  ringDisc: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: 'rgba(255,255,255,0.7)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  ringText: { ...theme.type.strong, color: '#5C3208' },
  else: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: theme.space.sm,
    marginTop: theme.space.md,
  },
  elseText: { ...theme.type.subtitle, color: theme.colour.textMuted },
});
