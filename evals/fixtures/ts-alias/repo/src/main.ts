import { add } from '@lib/math';
import config from '@config';
import { helper } from 'src/util/helper';
import { missing } from '@lib/nope';
import lodash from 'lodash';
import { shared } from '@acme/shared';
import { deep } from '@acme/shared/deep';
import { legacy } from '@acme/legacy';

export const total = add(config.base, helper()) + shared + deep + legacy + lodash.size(missing);
