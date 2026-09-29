import { describe, expect, it } from 'vitest';
import type { ToolCallInfo } from '../types';
import { formatImageCost, imageCost, imageToolPhase, imagesIn } from './generated-image';
import { useImagePresenter } from './image-presenter';

const call = (image: unknown, status: ToolCallInfo['status'] = 'success'): ToolCallInfo => ({
  id: 'c', tool: 'image_generate', arguments: '{}', status, metadata: { image },
});
const meta = (id: string, cost: number | null) => ({
  id, url: `/v1/images/${id}`, prompt: 'owl', kind: 'generate', model: 'gpt-image-2.5-flare', cost_usd: cost,
});

describe('imagesIn', () => {
  it('reads pictures off successful tool calls, in order', () => {
    expect(imagesIn([call(meta('img_a', 0.01)), call(meta('img_b', 0.02))]).map((i) => i.id))
      .toEqual(['img_a', 'img_b']);
  });

  it('ignores failed calls, missing metadata and foreign URLs', () => {
    expect(imagesIn([
      call(meta('img_a', 0.01), 'error'),
      call(undefined),
      call({ ...meta('img_x', 0), url: 'https://evil.example/x.png' }),
    ])).toEqual([]);
  });
});

describe('imageCost', () => {
  it('sums known costs', () => {
    const cost = imageCost([call(meta('a', 0.013)), call(meta('b', 0.021))])!;
    expect(cost.usd).toBeCloseTo(0.034);
    expect(formatImageCost(cost)).toBe('$0.034');
  });

  it('an unknown price is shown as unknown, never as $0', () => {
    expect(formatImageCost(imageCost([call(meta('a', null))])!)).toBe('cost unknown');
    expect(formatImageCost(imageCost([call(meta('a', 0.013)), call(meta('b', null))])!))
      .toBe('$0.013 + 1 unknown');
  });

  it('a reply with no pictures has no image cost', () => {
    expect(imageCost([])).toBeNull();
  });
});

describe('imageToolPhase', () => {
  it('says Drawing while a picture is made', () => {
    expect(imageToolPhase('image_generate')).toBe('Drawing...');
    expect(imageToolPhase('web_search')).toBeNull();
  });
});

describe('image presenter', () => {
  it('auto-opens a new picture once; history never reopens it', () => {
    const image = imagesIn([call(meta('img_once', 0.01))])[0];
    const presenter = useImagePresenter.getState();
    presenter.close();
    expect(presenter.showNew(image, true)).toBe(true);
    expect(useImagePresenter.getState().current?.id).toBe('img_once');
    useImagePresenter.getState().close();
    expect(useImagePresenter.getState().showNew(image, true)).toBe(false);
    expect(useImagePresenter.getState().current).toBeNull();
  });

  it('does not auto-open when the setting is off, but the card can still open it', () => {
    const image = imagesIn([call(meta('img_quiet', 0.01))])[0];
    const presenter = useImagePresenter.getState();
    presenter.close();
    expect(presenter.showNew(image, false)).toBe(false);
    expect(useImagePresenter.getState().current).toBeNull();
    useImagePresenter.getState().open(image);
    expect(useImagePresenter.getState().current?.id).toBe('img_quiet');
    useImagePresenter.getState().close();
  });
});
