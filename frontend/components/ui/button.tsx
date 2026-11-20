import Link from "next/link";
import type { RemixiconComponentType } from "@remixicon/react";
import type { ComponentProps } from "react";
import { buttonStyles } from "@/components/kits/zeus/base/buttons/button";
import { cx } from "@/lib/cx";

export { Button } from "@/components/kits/zeus/base/buttons/button";

interface LinkButtonProps extends ComponentProps<typeof Link> {
  variant?: "primary" | "secondary" | "ghost";
  size?: "medium" | "small" | "xs";
  trailingIcon?: RemixiconComponentType;
}

export function LinkButton({ variant = "secondary", size = "small", trailingIcon: Trailing, className, children, ...props }: LinkButtonProps) {
  return (
    <Link className={cx(buttonStyles.base, buttonStyles.size[size], buttonStyles.variant[variant], className)} {...props}>
      <span className={buttonStyles.label[size]}>{children}</span>
      {Trailing ? <Trailing className={buttonStyles.icon[size]} aria-hidden /> : null}
    </Link>
  );
}
