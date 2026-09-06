package video.minevideo.mixin;

import net.minecraft.client.render.GameRenderer;
import net.minecraft.client.render.RenderTickCounter;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
import video.minevideo.CaptureGuard;

@Mixin(GameRenderer.class)
public abstract class RendererMixin {
    @Inject(method = "renderWorld", at = @At("TAIL"))
    private void minevideoRendered(RenderTickCounter counter, CallbackInfo info) { CaptureGuard.renderedWorld(); }
}
