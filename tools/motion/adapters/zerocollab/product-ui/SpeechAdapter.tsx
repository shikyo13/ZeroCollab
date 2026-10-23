import { useContext } from 'react';
import { SpeechBubble as ProductSpeechBubble, type SpeechBubbleProps } from '../components/SpeechBubble';
import { UiPresentationContext } from './presentation';

/** Build-only adapter for CardSpeech's import. The product component and CSS are
 * shared; this maps authored bubble state to its controlled render seam. */
export function SpeechBubble(props: SpeechBubbleProps) {
  const state = useContext(UiPresentationContext);
  if (!state) throw new Error('UI speech needs its scripted frame context');
  const name = (id: string) => id.charAt(0).toUpperCase() + id.slice(1);
  const bubble = state.data.follow?.find(b => name(b.agent) === props.name);
  const previous = state.previous.follow?.find(b => b.agent === bubble?.agent && b.task === bubble?.task);
  return <ProductSpeechBubble {...props} open={bubble?.open ?? false}
    scrollTop={bubble?.scroll ?? 'end'}
    settle={{ fromText: previous?.text ?? '', fromOpen: previous?.open ?? false, ageMs: state.ageMs }} />;
}
